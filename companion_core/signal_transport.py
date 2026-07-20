"""Signal transport layer for M10 text chat.

The transport owns how messages physically enter and leave the system. It
never decides whether a message deserves a reply; that belongs to the chat
policy in ``signal_chat.py``. Raw signal-cli envelopes are parsed into
``InboundSignalMessage`` values and then dropped, so no raw envelope is ever
persisted.
"""

from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class SignalTransportError(RuntimeError):
    """Raised when the underlying Signal transport fails."""


class SignalCliUnavailableError(SignalTransportError):
    """Raised when signal-cli is not installed or not reachable."""


@dataclass(frozen=True)
class InboundSignalMessage:
    """One parsed inbound message with only the fields the chat loop needs."""

    sender: str
    timestamp: int
    body: str
    has_attachment: bool = False
    attachment_types: tuple[str, ...] = ()
    is_group: bool = False


def parse_signal_envelope_line(line: str) -> InboundSignalMessage | None:
    """Parse one signal-cli ``-o json receive`` output line.

    Returns ``None`` for anything that is not a human data message: receipts,
    typing indicators, sync messages, malformed JSON, or envelopes without a
    resolvable sender. Parsing never raises on bad input.
    """

    stripped = (line or "").strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    envelope = payload.get("envelope")
    if not isinstance(envelope, dict):
        return None
    sender = envelope.get("sourceNumber") or envelope.get("source")
    if not sender or not isinstance(sender, str):
        return None
    data_message = envelope.get("dataMessage")
    if not isinstance(data_message, dict):
        return None

    body = data_message.get("message")
    if not isinstance(body, str):
        body = ""
    attachments = data_message.get("attachments")
    attachment_types: list[str] = []
    if isinstance(attachments, list):
        for attachment in attachments:
            if isinstance(attachment, dict):
                attachment_types.append(str(attachment.get("contentType") or "unknown"))
    timestamp = envelope.get("timestamp")
    if not isinstance(timestamp, int):
        timestamp = 0
    return InboundSignalMessage(
        sender=sender,
        timestamp=timestamp,
        body=body,
        has_attachment=bool(attachment_types),
        attachment_types=tuple(attachment_types),
        is_group=isinstance(data_message.get("groupInfo"), dict),
    )


class FakeSignalTransport:
    """Deterministic in-memory transport for tests and dry runs."""

    name = "fake"

    def __init__(self, inbound_batches: list[list[InboundSignalMessage]] | None = None):
        self.inbound_batches: list[list[InboundSignalMessage]] = [
            list(batch) for batch in (inbound_batches or [])
        ]
        self.sent: list[dict] = []
        self.receive_calls = 0
        self.send_calls = 0
        self.fail_next_sends: int = 0

    def queue_batch(self, messages: list[InboundSignalMessage]) -> None:
        self.inbound_batches.append(list(messages))

    def receive(self) -> list[InboundSignalMessage]:
        self.receive_calls += 1
        if not self.inbound_batches:
            return []
        return self.inbound_batches.pop(0)

    def send(self, recipient: str, text: str) -> dict:
        self.send_calls += 1
        if self.fail_next_sends > 0:
            self.fail_next_sends -= 1
            raise SignalTransportError("fake transport send failure requested by test")
        record = {"recipient": recipient, "text": text}
        self.sent.append(record)
        return dict(record)


@dataclass
class SignalCliTransport:
    """signal-cli backed transport for real Raspberry Pi traffic.

    ``receive`` consumes pending messages from the signal-cli account and
    ``send`` delivers one text message under an exclusive file lock, matching
    the flock discipline of the legacy ``send_signal.sh``.
    """

    account: str
    signal_cli_bin: str = "signal-cli"
    receive_timeout_seconds: int = 5
    send_timeout_seconds: int = 60
    send_lock_file: Path | None = None
    name: str = field(default="signal-cli", init=False)

    def check_available(self) -> str:
        resolved = shutil.which(self.signal_cli_bin)
        if not resolved:
            raise SignalCliUnavailableError(
                f"signal-cli binary not found: {self.signal_cli_bin}"
            )
        return resolved

    def receive(self) -> list[InboundSignalMessage]:
        self.check_available()
        command = [
            self.signal_cli_bin,
            "-a",
            self.account,
            "-o",
            "json",
            "receive",
            "-t",
            str(self.receive_timeout_seconds),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.receive_timeout_seconds + 30,
            )
        except subprocess.TimeoutExpired as exc:
            raise SignalTransportError("signal-cli receive timed out") from exc
        if completed.returncode != 0:
            raise SignalTransportError(
                f"signal-cli receive failed with exit code {completed.returncode}"
            )
        messages = []
        for line in completed.stdout.splitlines():
            message = parse_signal_envelope_line(line)
            if message is not None:
                messages.append(message)
        return messages

    def send(self, recipient: str, text: str) -> dict:
        self.check_available()
        command = [
            self.signal_cli_bin,
            "-a",
            self.account,
            "send",
            "-m",
            text,
            recipient,
        ]
        lock_path = self.send_lock_file
        if lock_path is None:
            return self._run_send(command, recipient)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return self._run_send(command, recipient)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _run_send(self, command: list[str], recipient: str) -> dict:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.send_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise SignalTransportError("signal-cli send timed out") from exc
        if completed.returncode != 0:
            raise SignalTransportError(
                f"signal-cli send failed with exit code {completed.returncode}"
            )
        return {"recipient": recipient, "exit_code": completed.returncode}
