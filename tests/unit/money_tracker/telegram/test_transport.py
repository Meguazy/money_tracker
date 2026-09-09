"""Unit isolation tests for the official Bot API HTTP adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from money_tracker.messaging import BotEnvironment, OutboundButton, OutboxMessageDraft
from money_tracker.telegram import (
    BotApiClient,
    TelegramSettings,
    TelegramTransportError,
)


def _settings() -> TelegramSettings:
    """Return bounded test settings without reading a credential file."""

    return TelegramSettings(
        environment=BotEnvironment.TEST,
        bot_key="telegram-test",
        token_file=Path("/not/read"),
        poll_timeout_seconds=1,
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> BotApiClient:
    """Build production transport over an isolated HTTP boundary."""

    credential = "runtime:" + ("x" * 40)
    return BotApiClient(
        credential,
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_validate_disable_webhook_and_poll_parse_minimum_fields() -> None:
    """Call official methods and reduce updates without retaining raw payloads."""

    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.url.path.rsplit("/", 1)[-1], body))
        method = requests[-1][0]
        if method == "getMe":
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 9, "is_bot": True}}
            )
        if method == "deleteWebhook":
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 20,
                        "message": {
                            "message_id": 3,
                            "date": 1788940800,
                            "from": {"id": 1001, "is_bot": False},
                            "chat": {"id": 1001, "type": "private"},
                            "text": "Synthetic message",
                            "photo": [
                                {
                                    "file_id": "small",
                                    "file_unique_id": "small-stable",
                                    "width": 10,
                                    "height": 10,
                                    "file_size": 100,
                                },
                                {
                                    "file_id": "large",
                                    "file_unique_id": "large-stable",
                                    "width": 100,
                                    "height": 100,
                                    "file_size": 1000,
                                },
                            ],
                        },
                    },
                    {
                        "update_id": 21,
                        "callback_query": {
                            "id": "callback",
                            "from": {"id": 1001},
                            "data": "a:00000000-0000-0000-0000-000000000001:undo",
                            "message": {
                                "message_id": 4,
                                "date": 1788940801,
                                "chat": {"id": 1001, "type": "private"},
                            },
                        },
                    },
                ],
            },
        )

    transport = _client(handler)
    assert transport.validate_bot().bot_id == 9
    transport.disable_webhook()
    updates = transport.get_updates(offset=20)

    assert requests[1] == ("deleteWebhook", {"drop_pending_updates": False})
    assert requests[2][1]["offset"] == 20
    assert requests[2][1]["allowed_updates"] == ["message", "callback_query"]
    assert updates[0].image is not None
    assert updates[0].image.file_id == "large"
    assert updates[0].image.file_path is None
    assert updates[1].callback_query_id == "callback"
    assert not hasattr(updates[0], "raw_update")


def test_send_message_uses_plain_text_bounded_buttons_and_reply() -> None:
    """Send owner text without markup interpretation or internal target IDs."""

    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 77}},
        )

    transport = _client(handler)
    result = transport.send_message(
        OutboxMessageDraft(
            id=uuid4(),
            bot_key="telegram-test",
            telegram_chat_id=1001,
            logical_key="test:response",
            response_kind="help",
            text="<synthetic & plain>",
            content_hash="a" * 64,
            buttons=(
                OutboundButton(
                    "Annulla", "a:00000000-0000-0000-0000-000000000001:undo"
                ),
            ),
            reply_to_message_id=4,
        )
    )

    assert result.telegram_message_id == 77
    assert "parse_mode" not in observed
    assert observed["text"] == "<synthetic & plain>"
    assert observed["reply_markup"] == {
        "inline_keyboard": [
            [
                {
                    "text": "Annulla",
                    "callback_data": "a:00000000-0000-0000-0000-000000000001:undo",
                }
            ]
        ]
    }


@pytest.mark.parametrize(
    ("status_code", "payload", "expected_code", "transient"),
    [
        (401, {"ok": False, "description": "private"}, "authentication_failed", False),
        (
            429,
            {
                "ok": False,
                "description": "private",
                "parameters": {"retry_after": 12},
            },
            "rate_limited",
            True,
        ),
        (500, {"ok": False, "description": "private"}, "server_failure", True),
    ],
)
def test_failures_expose_only_safe_reason_codes(
    status_code: int,
    payload: dict[str, object],
    expected_code: str,
    transient: bool,
) -> None:
    """Prevent token-bearing URLs, bodies, and private descriptions in errors."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    with pytest.raises(TelegramTransportError) as captured:
        _client(handler).validate_bot()

    assert captured.value.code == expected_code
    assert captured.value.transient is transient
    assert "private" not in str(captured.value)
    assert "api.telegram.org" not in str(captured.value)
    assert "runtime:" not in str(captured.value)
