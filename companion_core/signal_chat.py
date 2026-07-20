"""M10 Signal text chat: config, policy, dedupe state, ledger, and bridge.

The bridge connects inbound Signal messages to the frozen M7 dialogue engine.
Every inbound message produces exactly one append-only attempt record with an
explicit decision, and replies go only to the sender of an allowed inbound
message. Memory stays proposal-only (``auto_memory=False``), so accepted
memory continues to flow exclusively through the M8 steward pipeline.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path

from .dialogue import DialogueRunner, append_turn_retraction
from .paths import CompanionPaths
from .signal_outbox import load_signal_outbox_entries
from .signal_transport import InboundSignalMessage

DEFAULT_POLL_INTERVAL_SECONDS = 10
DEFAULT_RECEIVE_TIMEOUT_SECONDS = 5
DEFAULT_DAILY_REPLY_BUDGET = 50
DEFAULT_MAX_REPLIES_PER_POLL = 3
DEFAULT_MAX_INBOUND_LENGTH = 4000
DEFAULT_QUIET_HOURS = ("00:00", "08:00")
DEFAULT_DAILY_OUTBOUND_BUDGET = 2
DEFAULT_OUTBOUND_QUIET_HOURS = ("00:00", "08:00")
DEFAULT_OUTBOUND_MAX_LENGTH = 900
DEFAULT_OUTBOUND_MAX_AGE_MINUTES = 360
DEFAULT_OUTBOUND_MAX_SEND_ATTEMPTS = 3

OUTBOUND_TERMINAL_STATUSES = ("delivered", "skipped", "abandoned")
SIGNAL_OUTBOUND_SKIP_REASONS = (
    "expired",
    "content_too_long",
    "recipient_missing",
    "duplicate_delivery",
    "abandoned_after_max_attempts",
)
SIGNAL_OUTBOUND_DEFER_REASONS = (
    "chat_paused",
    "outbound_paused",
    "quiet_hours",
    "daily_budget_exhausted",
)

SIGNAL_CHAT_SKIP_REASONS = (
    "paused",
    "group_message_unsupported",
    "sender_not_allowed",
    "duplicate_message",
    "empty_body",
    "attachment_only_unsupported",
    "body_too_long",
    "quiet_hours",
    "daily_budget_exhausted",
    "poll_batch_limit",
)

SIGNAL_CHAT_BOUNDARIES = {
    "wake_cycle_run": False,
    "scheduler_mutated": False,
    "proactive_outbound_sent": False,
    "raw_provider_payload_stored": False,
    "raw_signal_envelope_stored": False,
    "semantic_shadow_authority_promoted": False,
    "memory_authority_expanded": False,
    "voice_output": False,
}

M10_REQUIRED_FREEZE_EVIDENCE = (
    ("m7_dialogue_freeze_report.json", "m7_text_dialogue_frozen"),
    ("m8_memory_freeze_report.json", "m8_memory_dialogue_frozen"),
    ("m9_presence_freeze_report.json", "m9_controlled_presence_frozen"),
)


class SignalChatConfigError(RuntimeError):
    """Raised when the Signal chat config file is missing or invalid."""


class SignalChatLockError(RuntimeError):
    """Raised when another Signal chat bridge already holds the loop lock."""


@dataclass(frozen=True)
class SignalChatConfig:
    account: str
    allowed_senders: tuple[str, ...]
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    receive_timeout_seconds: int = DEFAULT_RECEIVE_TIMEOUT_SECONDS
    daily_reply_budget: int = DEFAULT_DAILY_REPLY_BUDGET
    max_replies_per_poll: int = DEFAULT_MAX_REPLIES_PER_POLL
    max_inbound_length: int = DEFAULT_MAX_INBOUND_LENGTH
    respect_quiet_hours: bool = False
    quiet_hours: tuple[str, str] = DEFAULT_QUIET_HOURS
    outbound_enabled: bool = False
    outbound_recipient: str | None = None
    daily_outbound_budget: int = DEFAULT_DAILY_OUTBOUND_BUDGET
    outbound_quiet_hours: tuple[str, str] = DEFAULT_OUTBOUND_QUIET_HOURS
    outbound_max_length: int = DEFAULT_OUTBOUND_MAX_LENGTH
    outbound_max_age_minutes: int = DEFAULT_OUTBOUND_MAX_AGE_MINUTES
    outbound_max_send_attempts: int = DEFAULT_OUTBOUND_MAX_SEND_ATTEMPTS

    def resolved_outbound_recipient(self) -> str | None:
        if self.outbound_recipient:
            return self.outbound_recipient
        return self.allowed_senders[0] if self.allowed_senders else None


def load_signal_chat_config(paths: CompanionPaths) -> SignalChatConfig:
    return _load_chat_config_file(paths.signal_chat_config_file, label="signal chat")


def load_feishu_chat_config(paths: CompanionPaths) -> SignalChatConfig:
    """Feishu reuses the channel-agnostic chat config schema from its own file.

    ``account`` holds the Feishu app_id; ``allowed_senders`` and
    ``outbound_recipient`` hold Feishu open_ids.
    """

    return _load_chat_config_file(paths.feishu_chat_config_file, label="feishu chat")


def _load_chat_config_file(config_path: Path, *, label: str) -> SignalChatConfig:
    if not config_path.exists():
        raise SignalChatConfigError(f"{label} config not found: {config_path}")
    try:
        payload = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise SignalChatConfigError(f"{label} config is invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise SignalChatConfigError(f"{label} config must be a JSON object")
    account = str(payload.get("account") or "").strip()
    if not account:
        raise SignalChatConfigError("chat config requires a non-empty 'account'")
    allowed = payload.get("allowed_senders")
    if not isinstance(allowed, list) or not allowed:
        raise SignalChatConfigError("chat config requires a non-empty 'allowed_senders' list")
    allowed_senders = tuple(str(sender).strip() for sender in allowed if str(sender).strip())
    if not allowed_senders:
        raise SignalChatConfigError("chat config requires at least one valid allowed sender")
    quiet_hours = _quiet_hours_field(payload, "quiet_hours", DEFAULT_QUIET_HOURS)
    outbound_quiet_hours = _quiet_hours_field(payload, "outbound_quiet_hours", DEFAULT_OUTBOUND_QUIET_HOURS)
    outbound_recipient = payload.get("outbound_recipient")
    if outbound_recipient is not None:
        outbound_recipient = str(outbound_recipient).strip() or None
    return SignalChatConfig(
        account=account,
        allowed_senders=allowed_senders,
        poll_interval_seconds=_positive_int(payload, "poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS),
        receive_timeout_seconds=_positive_int(payload, "receive_timeout_seconds", DEFAULT_RECEIVE_TIMEOUT_SECONDS),
        daily_reply_budget=_positive_int(payload, "daily_reply_budget", DEFAULT_DAILY_REPLY_BUDGET),
        max_replies_per_poll=_positive_int(payload, "max_replies_per_poll", DEFAULT_MAX_REPLIES_PER_POLL),
        max_inbound_length=_positive_int(payload, "max_inbound_length", DEFAULT_MAX_INBOUND_LENGTH),
        respect_quiet_hours=bool(payload.get("respect_quiet_hours", False)),
        quiet_hours=quiet_hours,
        outbound_enabled=bool(payload.get("outbound_enabled", False)),
        outbound_recipient=outbound_recipient,
        daily_outbound_budget=_positive_int(payload, "daily_outbound_budget", DEFAULT_DAILY_OUTBOUND_BUDGET),
        outbound_quiet_hours=outbound_quiet_hours,
        outbound_max_length=_positive_int(payload, "outbound_max_length", DEFAULT_OUTBOUND_MAX_LENGTH),
        outbound_max_age_minutes=_positive_int(payload, "outbound_max_age_minutes", DEFAULT_OUTBOUND_MAX_AGE_MINUTES),
        outbound_max_send_attempts=_positive_int(payload, "outbound_max_send_attempts", DEFAULT_OUTBOUND_MAX_SEND_ATTEMPTS),
    )


def _quiet_hours_field(payload: dict, key: str, default: tuple[str, str]) -> tuple[str, str]:
    value = payload.get(key) or list(default)
    if not isinstance(value, list) or len(value) != 2:
        raise SignalChatConfigError(f"chat config '{key}' must be [start, end]")
    for item in value:
        _parse_quiet_time(str(item))
    return (str(value[0]), str(value[1]))


def _positive_int(payload: dict, key: str, default: int) -> int:
    value = payload.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise SignalChatConfigError(f"chat config '{key}' must be an integer") from exc
    if value <= 0:
        raise SignalChatConfigError(f"chat config '{key}' must be positive")
    return value


def load_signal_chat_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    senders = payload.get("senders")
    daily = payload.get("daily")
    outbox = payload.get("outbox")
    outbound_daily = payload.get("outbound_daily")
    return {
        "schema_version": 1,
        "senders": senders if isinstance(senders, dict) else {},
        "daily": daily if isinstance(daily, dict) else {"date": None, "replies_sent": 0},
        "outbox": outbox if isinstance(outbox, dict) else {},
        "outbound_daily": outbound_daily
        if isinstance(outbound_daily, dict)
        else {"date": None, "delivered": 0},
    }


def save_signal_chat_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(path)


def append_signal_chat_attempts(path: Path, attempts: list[dict]) -> None:
    if not attempts:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(path.suffix + ".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a") as attempts_fd:
                for attempt in attempts:
                    attempts_fd.write(json.dumps(attempt, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def load_signal_chat_attempts(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def evaluate_signal_message(
    message: InboundSignalMessage,
    *,
    config: SignalChatConfig,
    state: dict,
    now: datetime,
    paused: bool,
    replies_this_poll: int,
) -> str | None:
    """Return a skip reason, or ``None`` when the message deserves a reply."""

    if paused:
        return "paused"
    if message.is_group:
        return "group_message_unsupported"
    if message.sender not in config.allowed_senders:
        return "sender_not_allowed"
    sender_state = state["senders"].get(message.sender) or {}
    last_timestamp = sender_state.get("last_timestamp")
    if isinstance(last_timestamp, int) and message.timestamp <= last_timestamp:
        return "duplicate_message"
    body = (message.body or "").strip()
    if not body:
        return "attachment_only_unsupported" if message.has_attachment else "empty_body"
    if len(body) > config.max_inbound_length:
        return "body_too_long"
    if config.respect_quiet_hours and _in_quiet_hours(now.time(), config.quiet_hours):
        return "quiet_hours"
    daily = _rolled_daily(state, now)
    if daily["replies_sent"] >= config.daily_reply_budget:
        return "daily_budget_exhausted"
    if replies_this_poll >= config.max_replies_per_poll:
        return "poll_batch_limit"
    return None


def load_m10_freeze_evidence(paths: CompanionPaths) -> dict:
    """Check the three freeze reports required before real Signal traffic."""

    reports = {}
    ok = True
    for name, expected in M10_REQUIRED_FREEZE_EVIDENCE:
        report_path = paths.life_loop_dir / name
        snapshot = {
            "path": _relative_to_home(paths, report_path),
            "exists": report_path.exists(),
            "expected_recommendation": expected,
            "recommendation": None,
            "ok": False,
        }
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text())
            except json.JSONDecodeError as exc:
                snapshot["error"] = f"invalid_json:{exc.msg}"
                report = {}
            recommendation = report.get("recommendation")
            snapshot["recommendation"] = recommendation
            snapshot["ok"] = bool(report.get("ok") is True and recommendation == expected)
        ok = ok and snapshot["ok"]
        reports[name] = snapshot
    return {"ok": ok, "reports": reports}


class StaticDialogueLLMClient:
    """Deterministic dialogue-shaped model substitute for fake and dry-run modes."""

    def __init__(self, reply_text: str = "我在。收到你的消息了，我们可以继续聊。"):
        self.reply_text = reply_text
        self.calls = 0

    def generate(self, prompt: str, context) -> str:
        self.calls += 1
        return self.reply_text


class FailingDialogueLLMClient:
    """Model substitute that always fails, for failure-path coverage."""

    def __init__(self, message: str = "provider unavailable for dry-run failure scenario"):
        self.message = message
        self.calls = 0

    def generate(self, prompt: str, context) -> str:
        self.calls += 1
        raise RuntimeError(self.message)


class SignalChatBridge:
    """Poll inbound Signal messages and answer allowed ones through M7 dialogue."""

    def __init__(
        self,
        paths: CompanionPaths,
        config: SignalChatConfig,
        transport,
        *,
        dialogue_runner: DialogueRunner | None = None,
        provider: str = "fake",
        memory_mode: str = "json",
        now_fn=None,
        mode: str = "live",
        lock_path: Path | None = None,
    ):
        self.paths = paths
        self.config = config
        self.transport = transport
        self.dialogue_runner = dialogue_runner
        self.provider = provider
        self.memory_mode = memory_mode
        self.now_fn = now_fn or datetime.now
        self.mode = mode
        self.lock_path = lock_path or paths.signal_chat_lock_file
        self.channel = getattr(transport, "channel", "signal")
        self.conversation_prefix = getattr(transport, "conversation_prefix", "signal")

    def poll_once(self) -> list[dict]:
        """Run one receive/decide/reply pass and return the attempt records."""

        if self.dialogue_runner is None:
            raise RuntimeError("inbound polling requires a dialogue runner")
        messages = self.transport.receive()
        if not messages:
            return []
        state = load_signal_chat_state(self.paths.signal_chat_state_file)
        attempts: list[dict] = []
        replies_this_poll = 0
        state_changed = False
        for message in messages:
            now = self.now_fn()
            paused = self.paths.signal_chat_pause_flag.exists()
            skip_reason = evaluate_signal_message(
                message,
                config=self.config,
                state=state,
                now=now,
                paused=paused,
                replies_this_poll=replies_this_poll,
            )
            if skip_reason is not None:
                attempts.append(self._attempt_record(message, now, decision="skipped", skip_reason=skip_reason))
                if skip_reason != "duplicate_message":
                    state_changed = self._advance_sender_state(state, message) or state_changed
                continue
            attempt = self._reply_to_message(message, now)
            attempts.append(attempt)
            state_changed = self._advance_sender_state(state, message) or state_changed
            if attempt["decision"] == "replied":
                replies_this_poll += 1
                daily = _rolled_daily(state, now)
                daily["replies_sent"] += 1
                state["daily"] = daily
                state_changed = True
        if state_changed:
            save_signal_chat_state(self.paths.signal_chat_state_file, state)
        append_signal_chat_attempts(self.paths.signal_chat_attempts_file, attempts)
        return attempts

    def run_loop(self, *, max_polls: int | None = None, sleep_fn=None) -> list[dict]:
        """Run the polling loop under the single-instance lock."""

        import time as time_module

        sleep_fn = sleep_fn or time_module.sleep
        all_attempts: list[dict] = []
        with self._loop_lock():
            polls = 0
            while max_polls is None or polls < max_polls:
                all_attempts.extend(self.poll_once())
                all_attempts.extend(self.deliver_outbox_once())
                polls += 1
                if max_polls is not None and polls >= max_polls:
                    break
                sleep_fn(self.config.poll_interval_seconds)
        return all_attempts

    def run_outbox_delivery(self, *, max_passes: int = 1) -> list[dict]:
        """Deliver pending outbox entries under the single-instance lock."""

        records: list[dict] = []
        with self._loop_lock():
            for _ in range(max_passes):
                records.extend(self.deliver_outbox_once())
        return records

    def deliver_outbox_once(self) -> list[dict]:
        """Run one outbound delivery pass. No-op unless outbound is enabled."""

        if not self.config.outbound_enabled:
            return []
        entries = load_signal_outbox_entries(self.paths.signal_outbox_file)
        if not entries:
            return []
        state = load_signal_chat_state(self.paths.signal_chat_state_file)
        outbox_state = state["outbox"]
        pending = [
            entry for entry in entries
            if entry.get("id")
            and (outbox_state.get(entry["id"]) or {}).get("status") not in OUTBOUND_TERMINAL_STATUSES
        ]
        if not pending:
            return []
        records: list[dict] = []
        state_changed = False
        delivered_events = {
            (outbox_state.get(entry["id"]) or {}).get("source_event_id") or entry.get("source_event_id")
            for entry in entries
            if entry.get("id") and (outbox_state.get(entry["id"]) or {}).get("status") == "delivered"
        }
        for entry in pending:
            now = self.now_fn()
            defer_reason = self._outbound_defer_reason(state, now)
            if defer_reason is not None:
                # Retryable conditions defer silently; entries stay pending and
                # the ledger is not spammed every poll interval.
                break
            entry_id = entry["id"]
            entry_state = dict(outbox_state.get(entry_id) or {})
            recipient = self.config.resolved_outbound_recipient()
            skip_reason = None
            if recipient is None:
                skip_reason = "recipient_missing"
            elif entry.get("source_event_id") and entry["source_event_id"] in delivered_events:
                skip_reason = "duplicate_delivery"
            elif _outbox_entry_age_minutes(entry, now) > self.config.outbound_max_age_minutes:
                skip_reason = "expired"
            elif int(entry.get("content_length") or len(str(entry.get("content") or ""))) > self.config.outbound_max_length:
                skip_reason = "content_too_long"
            if skip_reason is not None:
                outbox_state[entry_id] = {
                    "status": "skipped",
                    "skip_reason": skip_reason,
                    "attempts": entry_state.get("attempts", 0),
                    "source_event_id": entry.get("source_event_id"),
                    "updated_at": now.isoformat(),
                }
                records.append(self._outbound_record(entry, now, decision="skipped", skip_reason=skip_reason, recipient=recipient))
                state_changed = True
                continue
            attempts_before = int(entry_state.get("attempts", 0))
            try:
                send_attempts = self._send_with_retry(recipient, str(entry.get("content") or ""))
            except Exception as exc:  # noqa: BLE001 - delivery failures must land in evidence.
                total_attempts = attempts_before + 2
                if total_attempts >= self.config.outbound_max_send_attempts:
                    outbox_state[entry_id] = {
                        "status": "abandoned",
                        "attempts": total_attempts,
                        "source_event_id": entry.get("source_event_id"),
                        "updated_at": now.isoformat(),
                    }
                    records.append(self._outbound_record(
                        entry,
                        now,
                        decision="skipped",
                        skip_reason="abandoned_after_max_attempts",
                        recipient=recipient,
                        send_attempts=total_attempts,
                        error=exc,
                    ))
                else:
                    outbox_state[entry_id] = {
                        "status": "pending",
                        "attempts": total_attempts,
                        "source_event_id": entry.get("source_event_id"),
                        "updated_at": now.isoformat(),
                    }
                    records.append(self._outbound_record(
                        entry,
                        now,
                        decision="failed",
                        recipient=recipient,
                        send_attempts=total_attempts,
                        error=exc,
                    ))
                state_changed = True
                continue
            outbox_state[entry_id] = {
                "status": "delivered",
                "attempts": attempts_before + send_attempts,
                "source_event_id": entry.get("source_event_id"),
                "updated_at": now.isoformat(),
            }
            if entry.get("source_event_id"):
                delivered_events.add(entry["source_event_id"])
            outbound_daily = _rolled_outbound_daily(state, now)
            outbound_daily["delivered"] += 1
            records.append(self._outbound_record(
                entry,
                now,
                decision="delivered",
                recipient=recipient,
                send_attempts=send_attempts,
            ))
            state_changed = True
        if state_changed:
            save_signal_chat_state(self.paths.signal_chat_state_file, state)
        append_signal_chat_attempts(self.paths.signal_chat_attempts_file, records)
        return records

    def _outbound_defer_reason(self, state: dict, now: datetime) -> str | None:
        return outbound_defer_reason(self.paths, self.config, state, now)

    def _send_with_retry(self, recipient: str, text: str) -> int:
        """Send with one bounded retry; returns attempt count, raises last error."""

        try:
            self.transport.send(recipient, text)
            return 1
        except Exception:  # noqa: BLE001 - single bounded retry for transient failures.
            self.transport.send(recipient, text)
            return 2

    @contextmanager
    def _loop_lock(self):
        lock_path = self.lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SignalChatLockError(
                    f"another signal chat bridge already holds {lock_path}"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _outbound_record(
        self,
        entry: dict,
        now: datetime,
        *,
        decision: str,
        recipient: str | None,
        skip_reason: str | None = None,
        send_attempts: int = 0,
        error: Exception | None = None,
    ) -> dict:
        record = {
            "id": f"sigout_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}",
            "created_at": now.isoformat(),
            "direction": "outbound",
            "channel": self.channel,
            "mode": self.mode,
            "transport": getattr(self.transport, "name", type(self.transport).__name__),
            "outbox_entry_id": entry.get("id"),
            "source_event_id": entry.get("source_event_id"),
            "trigger": entry.get("trigger"),
            "recipient": recipient,
            "content_hash": entry.get("content_hash"),
            "content_length": entry.get("content_length"),
            "decision": decision,
            "skip_reason": skip_reason,
            "send_attempts": send_attempts,
            "boundaries": dict(SIGNAL_CHAT_BOUNDARIES),
            "error": None,
        }
        if error is not None:
            record["error"] = {
                "type": type(error).__name__,
                "message": " ".join(str(error).split())[:240],
            }
        return record

    def _reply_to_message(self, message: InboundSignalMessage, now: datetime) -> dict:
        conversation_id = channel_conversation_id(message.sender, prefix=self.conversation_prefix)
        try:
            result = self.dialogue_runner.run_turn(
                message.body.strip(),
                conversation_id=conversation_id,
                provider=self.provider,
                memory_mode=self.memory_mode,
                auto_memory=False,
            )
        except Exception as exc:  # noqa: BLE001 - every failure must land in the ledger.
            return self._attempt_record(message, now, decision="failed", error=exc)
        send_attempts = 0
        send_error: Exception | None = None
        try:
            send_attempts = self._send_with_retry(message.sender, result.reply)
        except Exception as exc:  # noqa: BLE001 - send failures must land in the ledger too.
            send_attempts = 2
            send_error = exc
        if send_error is not None:
            # The transcript already holds the generated reply, but the human
            # never received it. Retract the assistant turn so future prompt
            # context matches what was actually delivered; the transcript file
            # keeps the turn for audit.
            retraction = append_turn_retraction(
                result.transcript_path,
                turn_id=result.assistant_turn["id"],
                reason="signal_send_failed",
                channel="signal",
                error=send_error,
            )
            return self._attempt_record(
                message,
                now,
                decision="failed",
                error=send_error,
                conversation_id=result.conversation_id,
                dialogue_event_id=result.event.get("id"),
                reply=result.reply,
                send_attempts=send_attempts,
                retracted_turn_id=result.assistant_turn["id"],
                retraction_id=retraction["id"],
            )
        return self._attempt_record(
            message,
            now,
            decision="replied",
            conversation_id=result.conversation_id,
            dialogue_event_id=result.event.get("id"),
            reply=result.reply,
            memory_proposal_count=len(result.memory_proposals),
            send_attempts=send_attempts,
        )

    def _advance_sender_state(self, state: dict, message: InboundSignalMessage) -> bool:
        sender_state = state["senders"].setdefault(message.sender, {})
        last_timestamp = sender_state.get("last_timestamp")
        if not isinstance(last_timestamp, int) or message.timestamp > last_timestamp:
            sender_state["last_timestamp"] = message.timestamp
            return True
        return False

    def _attempt_record(
        self,
        message: InboundSignalMessage,
        now: datetime,
        *,
        decision: str,
        skip_reason: str | None = None,
        error: Exception | None = None,
        conversation_id: str | None = None,
        dialogue_event_id: str | None = None,
        reply: str | None = None,
        memory_proposal_count: int = 0,
        send_attempts: int = 0,
        retracted_turn_id: str | None = None,
        retraction_id: str | None = None,
    ) -> dict:
        record = {
            "id": f"sigchat_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}",
            "created_at": now.isoformat(),
            "direction": "inbound",
            "channel": self.channel,
            "mode": self.mode,
            "transport": getattr(self.transport, "name", type(self.transport).__name__),
            "provider": self.provider,
            "memory_mode": self.memory_mode,
            "sender": message.sender,
            "message_timestamp": message.timestamp,
            "body_hash": _sha256(message.body or ""),
            "body_length": len((message.body or "").strip()),
            "has_attachment": message.has_attachment,
            "is_group": message.is_group,
            "decision": decision,
            "skip_reason": skip_reason,
            "conversation_id": conversation_id,
            "dialogue_event_id": dialogue_event_id,
            "reply_hash": _sha256(reply) if reply else None,
            "reply_length": len(reply) if reply else 0,
            "memory_proposal_count": memory_proposal_count,
            "send_attempts": send_attempts,
            "retracted_turn_id": retracted_turn_id,
            "retraction_id": retraction_id,
            "boundaries": dict(SIGNAL_CHAT_BOUNDARIES),
            "error": None,
        }
        if error is not None:
            record["error"] = {
                "type": type(error).__name__,
                "message": " ".join(str(error).split())[:240],
            }
        return record


def signal_conversation_id(sender: str) -> str:
    return channel_conversation_id(sender, prefix="signal")


def channel_conversation_id(sender: str, *, prefix: str = "signal") -> str:
    safe_sender = re.sub(r"[^A-Za-z0-9]+", "", sender) or "unknown"
    return f"{prefix}_{safe_sender}"


def outbound_defer_reason(
    paths: CompanionPaths,
    config: SignalChatConfig,
    state: dict,
    now: datetime,
) -> str | None:
    """Retryable outbound hold reason, or ``None`` when delivery may proceed."""

    if paths.signal_chat_pause_flag.exists():
        return "chat_paused"
    if paths.signal_outbound_pause_flag.exists():
        return "outbound_paused"
    if _in_quiet_hours(now.time(), config.outbound_quiet_hours):
        return "quiet_hours"
    outbound_daily = _rolled_outbound_daily(state, now)
    if outbound_daily["delivered"] >= config.daily_outbound_budget:
        return "daily_budget_exhausted"
    return None


def _rolled_daily(state: dict, now: datetime) -> dict:
    daily = state.get("daily") or {}
    today = now.date().isoformat()
    if daily.get("date") != today:
        daily = {"date": today, "replies_sent": 0}
        state["daily"] = daily
    return daily


def _rolled_outbound_daily(state: dict, now: datetime) -> dict:
    daily = state.get("outbound_daily") or {}
    today = now.date().isoformat()
    if daily.get("date") != today:
        daily = {"date": today, "delivered": 0}
        state["outbound_daily"] = daily
    return daily


def _outbox_entry_age_minutes(entry: dict, now: datetime) -> float:
    created_raw = entry.get("created_at")
    try:
        created = datetime.fromisoformat(str(created_raw))
    except (TypeError, ValueError):
        return float("inf")
    return (now - created).total_seconds() / 60.0


def _in_quiet_hours(now_time: dt_time, quiet_hours: tuple[str, str]) -> bool:
    start = _parse_quiet_time(quiet_hours[0])
    end = _parse_quiet_time(quiet_hours[1])
    if start == end:
        return False
    if start < end:
        return start <= now_time < end
    return now_time >= start or now_time < end


def _parse_quiet_time(value: str) -> dt_time:
    try:
        hour, minute = value.strip().split(":")
        return dt_time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise SignalChatConfigError(f"invalid quiet hours time: {value!r}") from exc


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
