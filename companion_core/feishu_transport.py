"""M13 Feishu transport: event parsing, REST client, and bridge adapters.

Outbound messages go through the Feishu REST API with a cached tenant token
(stdlib urllib, no new HTTP dependency). Inbound messages arrive over the
official ``lark-oapi`` long-connection SDK, which is imported lazily so
machines without it (and every test) can inject fakes. The push-based long
connection adapts to the poll-based chat bridge through a thread-safe queue.

Raw Feishu event payloads are parsed into ``InboundSignalMessage`` values and
dropped; secrets stay in environment variables loaded from
``.secrets/feishu.env`` and never enter configs, reports, or the ledger.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue as queue_module
import threading
import time
import uuid
from pathlib import Path
from urllib import error, request

from .signal_transport import InboundSignalMessage

FEISHU_BASE_URL = "https://open.feishu.cn"
FEISHU_APP_ID_ENV = "FEISHU_APP_ID"
FEISHU_APP_SECRET_ENV = "FEISHU_APP_SECRET"
MESSAGE_EVENT_TYPE = "im.message.receive_v1"
# Feishu API codes that mean the tenant token expired or is invalid.
TOKEN_INVALID_CODES = (99991661, 99991663, 99991668)


class FeishuApiError(RuntimeError):
    """Raised when the Feishu REST API fails."""


class FeishuSdkUnavailableError(FeishuApiError):
    """Raised when the lark-oapi long-connection SDK is not installed."""


class FeishuCredentialsError(FeishuApiError):
    """Raised when the Feishu app credentials are missing."""


def parse_feishu_message_event(payload) -> InboundSignalMessage | None:
    """Parse one ``im.message.receive_v1`` event payload.

    Accepts either the full callback shape (``{"header": ..., "event": ...}``)
    or a bare event body. Returns ``None`` for non-message events, bot/system
    senders, or malformed payloads. Never raises on bad input.
    """

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    header = payload.get("header")
    if isinstance(header, dict):
        event_type = header.get("event_type")
        if event_type and event_type != MESSAGE_EVENT_TYPE:
            return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_type = sender.get("sender_type")
    if sender_type and sender_type != "user":
        return None
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    open_id = sender_id.get("open_id")
    if not open_id or not isinstance(open_id, str):
        return None

    message_type = message.get("message_type")
    body = ""
    if message_type == "text":
        try:
            content = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            content = {}
        if isinstance(content, dict):
            body = str(content.get("text") or "")
    try:
        timestamp = int(message.get("create_time") or 0)
    except (TypeError, ValueError):
        timestamp = 0
    is_attachment = message_type is not None and message_type != "text"
    return InboundSignalMessage(
        sender=open_id,
        timestamp=timestamp,
        body=body,
        has_attachment=is_attachment,
        attachment_types=(str(message_type),) if is_attachment else (),
        is_group=message.get("chat_type") == "group",
    )


class FeishuApiClient:
    """Minimal REST client: tenant token cache plus text-message sending."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = FEISHU_BASE_URL,
        timeout_seconds: int = 30,
        http_post=None,
        http_post_multipart=None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._http_post = http_post or self._default_http_post
        self._http_post_multipart = http_post_multipart or self._default_http_post_multipart
        self._token: str | None = None
        self._token_expiry = 0.0

    def _default_http_post(self, url: str, payload: dict, headers: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8", **headers},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                if isinstance(detail, dict):
                    return detail
            except (json.JSONDecodeError, OSError):
                pass
            raise FeishuApiError(f"feishu api http error: status={exc.code}") from exc
        except error.URLError as exc:
            raise FeishuApiError(f"feishu api unreachable: {exc.reason}") from exc

    def tenant_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and time.time() < self._token_expiry:
            return self._token
        data = self._http_post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
            {},
        )
        if not isinstance(data, dict) or data.get("code") != 0 or not data.get("tenant_access_token"):
            code = data.get("code") if isinstance(data, dict) else "unknown"
            raise FeishuApiError(f"feishu tenant token request failed: code={code}")
        self._token = str(data["tenant_access_token"])
        expire_seconds = int(data.get("expire") or 3600)
        self._token_expiry = time.time() + max(60, expire_seconds - 300)
        return self._token

    def send_text(self, open_id: str, text: str) -> dict:
        url = f"{self.base_url}/open-apis/im/v1/messages?receive_id_type=open_id"
        payload = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        data = self._authorized_post(url, payload)
        if isinstance(data, dict) and data.get("code") in TOKEN_INVALID_CODES:
            # One bounded re-auth retry when the cached token went stale.
            self.tenant_access_token(force_refresh=True)
            data = self._authorized_post(url, payload)
        if not isinstance(data, dict) or data.get("code") != 0:
            code = data.get("code") if isinstance(data, dict) else "unknown"
            raise FeishuApiError(f"feishu send failed: code={code}")
        message_id = (data.get("data") or {}).get("message_id") if isinstance(data.get("data"), dict) else None
        return {"message_id": message_id}

    def _authorized_post(self, url: str, payload: dict) -> dict:
        token = self.tenant_access_token()
        return self._http_post(url, payload, {"Authorization": f"Bearer {token}"})

    def upload_image(self, filename: str, data_bytes: bytes) -> str:
        """Upload one image (validated byte snapshot) and return its image_key."""

        data = self._authorized_multipart(
            f"{self.base_url}/open-apis/im/v1/images",
            fields={"image_type": "message"},
            file_field="image",
            filename=filename,
            file_bytes=data_bytes,
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            code = data.get("code") if isinstance(data, dict) else "unknown"
            raise FeishuApiError(f"feishu image upload failed: code={code}")
        image_key = (data.get("data") or {}).get("image_key") if isinstance(data.get("data"), dict) else None
        if not image_key:
            raise FeishuApiError("feishu image upload returned no image_key")
        return str(image_key)

    def upload_opus(self, filename: str, data_bytes: bytes, duration_ms: int) -> str:
        """Upload one opus audio byte snapshot and return its file_key."""

        data = self._authorized_multipart(
            f"{self.base_url}/open-apis/im/v1/files",
            fields={
                "file_type": "opus",
                "file_name": filename,
                "duration": str(int(duration_ms)),
            },
            file_field="file",
            filename=filename,
            file_bytes=data_bytes,
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            code = data.get("code") if isinstance(data, dict) else "unknown"
            raise FeishuApiError(f"feishu audio upload failed: code={code}")
        file_key = (data.get("data") or {}).get("file_key") if isinstance(data.get("data"), dict) else None
        if not file_key:
            raise FeishuApiError("feishu audio upload returned no file_key")
        return str(file_key)

    def send_image(self, open_id: str, image_key: str) -> dict:
        return self._send_message(open_id, "image", {"image_key": image_key})

    def send_audio(self, open_id: str, file_key: str, duration_ms: int) -> dict:
        return self._send_message(open_id, "audio", {"file_key": file_key, "duration": int(duration_ms)})

    def _send_message(self, open_id: str, msg_type: str, content: dict) -> dict:
        url = f"{self.base_url}/open-apis/im/v1/messages?receive_id_type=open_id"
        payload = {
            "receive_id": open_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        data = self._authorized_post(url, payload)
        if isinstance(data, dict) and data.get("code") in TOKEN_INVALID_CODES:
            self.tenant_access_token(force_refresh=True)
            data = self._authorized_post(url, payload)
        if not isinstance(data, dict) or data.get("code") != 0:
            code = data.get("code") if isinstance(data, dict) else "unknown"
            raise FeishuApiError(f"feishu {msg_type} send failed: code={code}")
        message_id = (data.get("data") or {}).get("message_id") if isinstance(data.get("data"), dict) else None
        return {"message_id": message_id}

    def _authorized_multipart(
        self,
        url: str,
        *,
        fields: dict,
        file_field: str,
        filename: str,
        file_bytes: bytes,
    ) -> dict:
        token = self.tenant_access_token()
        data = self._http_post_multipart(
            url,
            fields,
            file_field,
            filename,
            file_bytes,
            {"Authorization": f"Bearer {token}"},
        )
        if isinstance(data, dict) and data.get("code") in TOKEN_INVALID_CODES:
            token = self.tenant_access_token(force_refresh=True)
            data = self._http_post_multipart(
                url,
                fields,
                file_field,
                filename,
                file_bytes,
                {"Authorization": f"Bearer {token}"},
            )
        return data

    def _default_http_post_multipart(
        self,
        url: str,
        fields: dict,
        file_field: str,
        filename: str,
        file_bytes: bytes,
        headers: dict,
    ) -> dict:
        body, content_type = encode_multipart_form(fields, file_field, filename, file_bytes)
        http_request = request.Request(
            url,
            data=body,
            headers={"Content-Type": content_type, **headers},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=max(self.timeout_seconds, 60)) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                if isinstance(detail, dict):
                    return detail
            except (json.JSONDecodeError, OSError):
                pass
            raise FeishuApiError(f"feishu upload http error: status={exc.code}") from exc
        except error.URLError as exc:
            raise FeishuApiError(f"feishu api unreachable: {exc.reason}") from exc


_HEADER_UNSAFE_RE = None


def _sanitize_header_value(value: str) -> str:
    """Strip CRLF and quotes so form values cannot inject multipart headers."""

    return str(value or "").replace("\r", "").replace("\n", "").replace('"', "").strip() or "file"


def encode_multipart_form(
    fields: dict,
    file_field: str,
    filename: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    """Encode a multipart/form-data body with one file part (stdlib only)."""

    boundary = f"companion-boundary-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        safe_key = _sanitize_header_value(key)
        safe_value = str(value or "").replace("\r", "").replace("\n", "")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{safe_key}"\r\n\r\n'
                f"{safe_value}\r\n"
            ).encode("utf-8")
        )
    safe_filename = _sanitize_header_value(filename)
    mime_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{_sanitize_header_value(file_field)}"; filename="{safe_filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class FeishuTransport:
    """Bridge-facing Feishu transport: queue-backed receive, REST send."""

    name = "feishu"
    channel = "feishu"
    conversation_prefix = "feishu"
    supports_media = True

    def __init__(
        self,
        app_id: str | None = None,
        *,
        api: FeishuApiClient | None = None,
        timeout_seconds: int = 30,
        require_listener: bool = True,
    ):
        self.app_id = app_id or os.environ.get(FEISHU_APP_ID_ENV)
        self.timeout_seconds = timeout_seconds
        self.require_listener = require_listener
        self._api_instance = api
        self._queue: queue_module.Queue = queue_module.Queue()
        self._listener_thread: threading.Thread | None = None
        self.listener_error: str | None = None

    def enqueue(self, message: InboundSignalMessage) -> None:
        self._queue.put(message)

    def enqueue_event(self, payload) -> bool:
        """Parse a raw event payload and enqueue it when it is a user message."""

        message = parse_feishu_message_event(payload)
        if message is None:
            return False
        self._queue.put(message)
        return True

    def receive(self) -> list[InboundSignalMessage]:
        messages: list[InboundSignalMessage] = []
        while True:
            try:
                messages.append(self._queue.get_nowait())
            except queue_module.Empty:
                break
        return messages

    def send(self, recipient: str, text: str) -> dict:
        return self._api().send_text(recipient, text)

    def send_image(self, recipient: str, filename: str, data_bytes: bytes) -> dict:
        api = self._api()
        image_key = api.upload_image(filename, data_bytes)
        return api.send_image(recipient, image_key)

    def send_voice(self, recipient: str, opus_path: Path, duration_ms: int) -> dict:
        opus_path = Path(opus_path)
        api = self._api()
        file_key = api.upload_opus(opus_path.name, opus_path.read_bytes(), duration_ms)
        return api.send_audio(recipient, file_key, duration_ms)

    def check_available(self) -> str:
        """Verify credentials, token issuance, and (when required) the SDK."""

        api = self._api()
        api.tenant_access_token()
        if self.require_listener:
            _import_lark_oapi()
        return self.app_id or "unknown-app"

    def start_listener(self) -> None:
        """Start the lark-oapi long-connection client in a daemon thread."""

        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        lark = _import_lark_oapi()
        app_secret = os.environ.get(FEISHU_APP_SECRET_ENV)
        if not self.app_id or not app_secret:
            raise FeishuCredentialsError(
                f"feishu credentials missing: set {FEISHU_APP_ID_ENV} and {FEISHU_APP_SECRET_ENV}"
            )

        def on_message(data) -> None:
            try:
                payload = json.loads(lark.JSON.marshal(data))
            except Exception:  # noqa: BLE001 - listener must never die on one event.
                return
            self.enqueue_event(payload)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        client = lark.ws.Client(
            self.app_id,
            app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        def run_client() -> None:
            try:
                client.start()
            except Exception as exc:  # noqa: BLE001 - surfaced through listener_error.
                self.listener_error = f"{type(exc).__name__}: {exc}"

        self._listener_thread = threading.Thread(
            target=run_client,
            name="feishu-long-connection",
            daemon=True,
        )
        self._listener_thread.start()

    def _api(self) -> FeishuApiClient:
        if self._api_instance is not None:
            return self._api_instance
        app_secret = os.environ.get(FEISHU_APP_SECRET_ENV)
        if not self.app_id or not app_secret:
            raise FeishuCredentialsError(
                f"feishu credentials missing: set {FEISHU_APP_ID_ENV} and {FEISHU_APP_SECRET_ENV}"
            )
        self._api_instance = FeishuApiClient(
            self.app_id,
            app_secret,
            timeout_seconds=self.timeout_seconds,
        )
        return self._api_instance


class FakeFeishuTransport:
    """Deterministic in-memory Feishu stand-in for tests and dry runs."""

    name = "feishu-fake"
    channel = "feishu"
    conversation_prefix = "feishu"
    supports_media = True

    def __init__(self, inbound_batches: list[list[InboundSignalMessage]] | None = None):
        self.inbound_batches: list[list[InboundSignalMessage]] = [
            list(batch) for batch in (inbound_batches or [])
        ]
        self.sent: list[dict] = []
        self.sent_images: list[dict] = []
        self.sent_voices: list[dict] = []
        self.receive_calls = 0
        self.send_calls = 0
        self.fail_next_sends: int = 0
        self.fail_next_image_sends: int = 0
        self.fail_next_voice_sends: int = 0

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
            raise FeishuApiError("fake feishu send failure requested by test")
        record = {"recipient": recipient, "text": text}
        self.sent.append(record)
        return dict(record)

    def send_image(self, recipient: str, filename: str, data_bytes: bytes) -> dict:
        if self.fail_next_image_sends > 0:
            self.fail_next_image_sends -= 1
            raise FeishuApiError("fake feishu image send failure requested by test")
        record = {"recipient": recipient, "filename": str(filename), "size": len(data_bytes)}
        self.sent_images.append(record)
        return {"message_id": f"om_img_{len(self.sent_images)}"}

    def send_voice(self, recipient: str, opus_path: Path, duration_ms: int) -> dict:
        if self.fail_next_voice_sends > 0:
            self.fail_next_voice_sends -= 1
            raise FeishuApiError("fake feishu voice send failure requested by test")
        record = {"recipient": recipient, "path": str(opus_path), "duration_ms": int(duration_ms)}
        self.sent_voices.append(record)
        return {"message_id": f"om_voice_{len(self.sent_voices)}"}


def _import_lark_oapi():
    try:
        import lark_oapi  # type: ignore
    except ImportError as exc:
        raise FeishuSdkUnavailableError(
            "lark-oapi is not installed; install it on the machine running the Feishu listener"
        ) from exc
    return lark_oapi
