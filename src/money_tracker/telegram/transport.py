"""Official Telegram Bot API adapter with bounded, redacted failures."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from money_tracker.messaging import OutboxMessageDraft
from money_tracker.telegram.config import TelegramSettings
from money_tracker.telegram.contracts import (
    BotIdentity,
    TelegramFile,
    TelegramInboundUpdate,
    TelegramSentMessage,
)

_ALLOWED_UPDATES = ("message", "callback_query")
_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}


class TelegramTransportError(RuntimeError):
    """Safe transport failure without sensitive transport or owner data.

    Attributes:
        code: Stable content-free reason used by retry and diagnostics.
        transient: Whether retry can succeed without operator correction.
        retry_after_seconds: Bounded Bot API delay hint when present.
    """

    def __init__(
        self,
        code: str,
        *,
        transient: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Build retry-safe failure metadata."""

        super().__init__(f"Telegram transport failure: {code}")
        self.code = code
        self.transient = transient
        self.retry_after_seconds = retry_after_seconds


class TelegramTransport(Protocol):
    """Narrow Bot API operations used by gateway application services."""

    def validate_bot(self) -> BotIdentity:
        """Validate the selected credential."""

    def disable_webhook(self) -> None:
        """Select long polling without dropping pending updates."""

    def get_updates(self, *, offset: int) -> tuple[TelegramInboundUpdate, ...]:
        """Long poll one ordered typed transport batch."""

    def get_file(
        self,
        *,
        file_id: str,
        file_unique_id: str,
        media_type: str,
    ) -> TelegramFile:
        """Resolve metadata without constructing a download URL."""

    def send_message(self, message: OutboxMessageDraft) -> TelegramSentMessage:
        """Deliver one durable logical outbox message."""

    def answer_callback_query(self, callback_query_id: str) -> None:
        """Stop the callback indicator after durable ingestion."""

    def close(self) -> None:
        """Release HTTP resources."""


class BotApiClient:
    """Synchronous Bot API client for one explicit bot credential.

    Attributes:
        The adapter retains only private HTTP/configuration implementation state
        and exposes no credential, URL, or raw response attribute.
    """

    def __init__(
        self,
        token: str,
        settings: TelegramSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        """Build the adapter and bounded timeout policy.

        Args:
            token: Credential read from the selected owner-only file.
            settings: Explicit environment and bounded timeout settings.
            client: Optional caller-owned HTTP client for unit isolation.
        """

        if not token or any(character.isspace() for character in token):
            raise ValueError("Telegram credential is invalid")
        self._settings = settings
        self._api_base = f"https://api.telegram.org/bot{token}"
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.poll_timeout_seconds + settings.request_timeout_seconds,
                write=settings.request_timeout_seconds,
                pool=settings.connect_timeout_seconds,
            ),
            headers={"User-Agent": "money-tracker-telegram/1"},
        )

    def validate_bot(self) -> BotIdentity:
        """Validate the token without returning identity metadata.

        Returns:
            Minimal positive bot identity proving the credential is valid.
        """

        result = _mapping(self._request("getMe", {}), code="invalid_get_me")
        if result.get("is_bot") is not True:
            raise TelegramTransportError("credential_not_bot", transient=False)
        return BotIdentity(_integer(result.get("id"), code="invalid_get_me"))

    def disable_webhook(self) -> None:
        """Delete a webhook while preserving every pending update."""

        self._request("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, *, offset: int) -> tuple[TelegramInboundUpdate, ...]:
        """Return one long-poll batch reduced to minimum parsed fields.

        Args:
            offset: Persisted next update offset.

        Returns:
            Ordered typed updates with no retained raw envelope.
        """

        if offset < 0:
            raise ValueError("Telegram polling offset cannot be negative")
        result = self._request(
            "getUpdates",
            {
                "offset": offset,
                "limit": 100,
                "timeout": int(self._settings.poll_timeout_seconds),
                "allowed_updates": list(_ALLOWED_UPDATES),
            },
        )
        if not isinstance(result, list):
            raise TelegramTransportError("invalid_updates", transient=True)
        parsed = tuple(_parse_update(item) for item in result)
        identifiers = tuple(update.update_id for update in parsed)
        if identifiers != tuple(sorted(set(identifiers))):
            raise TelegramTransportError("unordered_updates", transient=True)
        return parsed

    def get_file(
        self,
        *,
        file_id: str,
        file_unique_id: str,
        media_type: str,
    ) -> TelegramFile:
        """Resolve safe metadata and retain only an opaque relative file path.

        Args:
            file_id: Opaque Bot API lookup identity.
            file_unique_id: Expected stable file identity.
            media_type: Previously authorized image media type.

        Returns:
            Verified metadata with no token-bearing download URL.
        """

        if not file_id or not file_unique_id:
            raise ValueError("Telegram file identity is incomplete")
        result = _mapping(
            self._request("getFile", {"file_id": file_id}),
            code="invalid_file",
        )
        if (
            _string(result.get("file_id"), code="invalid_file") != file_id
            or _string(result.get("file_unique_id"), code="invalid_file")
            != file_unique_id
        ):
            raise TelegramTransportError("file_identity_changed", transient=False)
        return TelegramFile(
            file_id=file_id,
            file_unique_id=file_unique_id,
            media_type=media_type,
            file_size=_optional_integer(result.get("file_size"), code="invalid_file"),
            file_path=_optional_string(result.get("file_path"), code="invalid_file"),
        )

    def send_message(self, message: OutboxMessageDraft) -> TelegramSentMessage:
        """Deliver plain text and bounded inline controls.

        Args:
            message: Durable outbox message claim content.

        Returns:
            Minimal Telegram message identity for durable correlation.
        """

        parameters: dict[str, object] = {
            "chat_id": message.telegram_chat_id,
            "text": message.text,
        }
        if message.buttons:
            parameters["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": button.label, "callback_data": button.callback_data}]
                    for button in message.buttons
                ]
            }
        if message.reply_to_message_id is not None:
            parameters["reply_parameters"] = {
                "message_id": message.reply_to_message_id,
                "allow_sending_without_reply": True,
            }
        result = _mapping(
            self._request("sendMessage", parameters),
            code="invalid_send",
        )
        return TelegramSentMessage(
            _integer(result.get("message_id"), code="invalid_send")
        )

    def answer_callback_query(self, callback_query_id: str) -> None:
        """Acknowledge a callback only after its update is durable.

        Args:
            callback_query_id: Opaque callback identity from the committed row.
        """

        if not callback_query_id or len(callback_query_id) > 256:
            raise ValueError("callback query identity is invalid")
        self._request("answerCallbackQuery", {"callback_query_id": callback_query_id})

    def close(self) -> None:
        """Close an internally created client."""

        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BotApiClient:
        """Return this adapter for deterministic cleanup."""

        return self

    def __exit__(self, *_: object) -> None:
        """Release owned resources."""

        self.close()

    def _request(self, method: str, parameters: Mapping[str, object]) -> object:
        """Call one method and translate every failure to safe metadata."""

        timeout = (
            httpx.Timeout(
                connect=self._settings.connect_timeout_seconds,
                read=self._settings.poll_timeout_seconds
                + self._settings.request_timeout_seconds,
                write=self._settings.request_timeout_seconds,
                pool=self._settings.connect_timeout_seconds,
            )
            if method == "getUpdates"
            else self._settings.request_timeout_seconds
        )
        try:
            response = self._client.post(
                f"{self._api_base}/{method}",
                json=dict(parameters),
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise TelegramTransportError(
                "network_unavailable", transient=True
            ) from None
        except httpx.HTTPError:
            raise TelegramTransportError("http_failure", transient=True) from None
        try:
            payload = response.json()
        except ValueError:
            raise TelegramTransportError("invalid_json", transient=True) from None
        if not isinstance(payload, dict):
            raise TelegramTransportError("invalid_envelope", transient=True)
        if response.status_code == 200 and payload.get("ok") is True:
            if "result" not in payload:
                raise TelegramTransportError("missing_result", transient=True)
            return payload["result"]
        api_code = payload.get("error_code")
        retry_after = _retry_after(payload)
        if response.status_code == 429 or api_code == 429:
            raise TelegramTransportError(
                "rate_limited",
                transient=True,
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 500:
            raise TelegramTransportError("server_failure", transient=True)
        if response.status_code in {401, 403}:
            raise TelegramTransportError("authentication_failed", transient=False)
        if response.status_code == 409:
            raise TelegramTransportError("webhook_conflict", transient=False)
        raise TelegramTransportError("request_rejected", transient=False)


def _parse_update(value: object) -> TelegramInboundUpdate:
    """Reduce a raw update object to a transient typed value."""

    update = _mapping(value, code="invalid_update")
    update_id = _integer(update.get("update_id"), code="invalid_update")
    if "message" in update:
        return _parse_message(update_id, update["message"])
    if "callback_query" in update:
        return _parse_callback(update_id, update["callback_query"])
    return TelegramInboundUpdate(
        update_id=update_id,
        kind="unsupported",
        telegram_user_id=None,
        telegram_chat_id=None,
        chat_type=None,
        received_at=datetime.now(UTC),
    )


def _parse_message(update_id: int, value: object) -> TelegramInboundUpdate:
    """Parse fields needed to authorize and durably normalize a message."""

    message = _mapping(value, code="invalid_update")
    sender = _optional_mapping(message.get("from"), code="invalid_update")
    chat = _mapping(message.get("chat"), code="invalid_update")
    reply = _optional_mapping(message.get("reply_to_message"), code="invalid_update")
    return TelegramInboundUpdate(
        update_id=update_id,
        kind="message",
        telegram_user_id=(
            None
            if sender is None
            else _integer(sender.get("id"), code="invalid_update")
        ),
        telegram_chat_id=_integer(chat.get("id"), code="invalid_update"),
        chat_type=_string(chat.get("type"), code="invalid_update"),
        received_at=_telegram_time(message.get("date")),
        telegram_message_id=_integer(message.get("message_id"), code="invalid_update"),
        text=_optional_string(
            message.get("text", message.get("caption")),
            code="invalid_update",
        ),
        reply_to_message_id=(
            None
            if reply is None
            else _integer(reply.get("message_id"), code="invalid_update")
        ),
        image=_message_image(message),
    )


def _parse_callback(update_id: int, value: object) -> TelegramInboundUpdate:
    """Parse callback scope without trusting its target data."""

    callback = _mapping(value, code="invalid_update")
    sender = _mapping(callback.get("from"), code="invalid_update")
    message = _mapping(callback.get("message"), code="invalid_update")
    chat = _mapping(message.get("chat"), code="invalid_update")
    return TelegramInboundUpdate(
        update_id=update_id,
        kind="callback_query",
        telegram_user_id=_integer(sender.get("id"), code="invalid_update"),
        telegram_chat_id=_integer(chat.get("id"), code="invalid_update"),
        chat_type=_string(chat.get("type"), code="invalid_update"),
        received_at=_telegram_time(message.get("date")),
        telegram_message_id=_integer(message.get("message_id"), code="invalid_update"),
        callback_data=_optional_string(callback.get("data"), code="invalid_update"),
        callback_query_id=_string(callback.get("id"), code="invalid_update"),
    )


def _message_image(message: Mapping[str, object]) -> TelegramFile | None:
    """Select the largest photo or supported image document metadata."""

    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        candidates = [_mapping(item, code="invalid_update") for item in photos]
        selected = max(
            candidates,
            key=lambda item: (
                _optional_integer(item.get("file_size"), code="invalid_update") or 0,
                _optional_integer(item.get("width"), code="invalid_update") or 0,
                _optional_integer(item.get("height"), code="invalid_update") or 0,
            ),
        )
        return TelegramFile(
            file_id=_string(selected.get("file_id"), code="invalid_update"),
            file_unique_id=_string(
                selected.get("file_unique_id"), code="invalid_update"
            ),
            media_type="image/jpeg",
            file_size=_optional_integer(
                selected.get("file_size"), code="invalid_update"
            ),
        )
    document = _optional_mapping(message.get("document"), code="invalid_update")
    if document is None:
        return None
    media_type = _optional_string(document.get("mime_type"), code="invalid_update")
    if media_type not in _IMAGE_MEDIA_TYPES:
        return None
    return TelegramFile(
        file_id=_string(document.get("file_id"), code="invalid_update"),
        file_unique_id=_string(document.get("file_unique_id"), code="invalid_update"),
        media_type=media_type,
        file_size=_optional_integer(document.get("file_size"), code="invalid_update"),
    )


def _retry_after(payload: Mapping[str, object]) -> int | None:
    """Return only a bounded server retry hint."""

    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= 3600 else None


def _mapping(value: object, *, code: str) -> dict[str, object]:
    """Require an object response without echoing it."""

    if not isinstance(value, dict):
        raise TelegramTransportError(code, transient=True)
    return cast(dict[str, object], value)


def _optional_mapping(value: object, *, code: str) -> dict[str, object] | None:
    """Parse an absent or object response field."""

    return None if value is None else _mapping(value, code=code)


def _integer(value: object, *, code: str) -> int:
    """Require a non-boolean integer response field."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TelegramTransportError(code, transient=True)
    return value


def _optional_integer(value: object, *, code: str) -> int | None:
    """Parse an absent or integer response field."""

    return None if value is None else _integer(value, code=code)


def _string(value: object, *, code: str) -> str:
    """Require a non-empty string response field."""

    if not isinstance(value, str) or not value:
        raise TelegramTransportError(code, transient=True)
    return value


def _optional_string(value: object, *, code: str) -> str | None:
    """Parse an absent or string response field."""

    return None if value is None else _string(value, code=code)


def _telegram_time(value: object) -> datetime:
    """Convert a Unix timestamp to aware UTC."""

    try:
        return datetime.fromtimestamp(_integer(value, code="invalid_update"), UTC)
    except (OverflowError, OSError, ValueError):
        raise TelegramTransportError("invalid_update", transient=True) from None
