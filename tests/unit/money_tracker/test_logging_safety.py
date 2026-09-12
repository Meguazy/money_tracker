"""Behavior tests proving no handler can emit a Bot API credential."""

from __future__ import annotations

import logging

import pytest

from money_tracker.logging_safety import (
    STRUCTURED_FIELDS_ATTRIBUTE,
    CredentialFilter,
    quiet_http_loggers,
    redact_credentials,
    redact_value,
)

TOKEN = "8435117429:AAF_-SEBbKbyAdP3FRUYCbJpk36Ypt5qXQo"
REQUEST_LINE = (
    f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getMe "HTTP/1.1 200 OK"'
)


def _record(message: str, args: object = ()) -> logging.LogRecord:
    """Build one record the way a library adapter would."""

    return logging.LogRecord(
        name="money_tracker.some.future.adapter",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_a_logged_request_url_loses_its_credential() -> None:
    """Redact the exact shape an HTTP client logs for the Bot API."""

    redacted = redact_credentials(REQUEST_LINE)

    assert TOKEN not in redacted
    assert "bot<redacted>/getMe" in redacted
    assert "api.telegram.org" in redacted


def test_a_bare_token_is_redacted_too() -> None:
    """Catch a credential logged without the URL prefix that names it."""

    assert TOKEN not in redact_credentials(f"token={TOKEN} failed")


def test_ordinary_text_survives_redaction() -> None:
    """Keep diagnostics readable instead of redacting everything with a colon."""

    message = "Telegram ingest committed accepted=0 rejected=0 unsupported=1"

    assert redact_credentials(message) == message
    assert redact_credentials("2026-09-09 22:17:14,772 INFO") == (
        "2026-09-09 22:17:14,772 INFO"
    )


def test_a_non_string_field_keeps_its_type() -> None:
    """Leave a count or an identifier alone so a field still means what it says."""

    assert redact_value(42) == 42
    assert redact_value(None) is None
    assert redact_value(f"bot{TOKEN}/getMe") == "bot<redacted>/getMe"


def test_the_filter_cleans_a_message_and_its_arguments() -> None:
    """Defend against the next library that logs a URL we did not expect.

    The credential arrives as a `%s` argument rather than inside the template,
    which is how a real adapter would log it and what a filter that only
    inspected the message would miss.
    """

    record = _record("call failed for %s", (f"https://api.telegram.org/bot{TOKEN}",))

    assert CredentialFilter().filter(record) is True
    assert TOKEN not in record.getMessage()
    assert "bot<redacted>" in record.getMessage()


def test_the_filter_cleans_structured_fields() -> None:
    """Apply the same defence to a field, which a formatter renders later."""

    record = _record("transport.request.rejected")
    setattr(record, STRUCTURED_FIELDS_ATTRIBUTE, {"url": f"bot{TOKEN}/getMe"})

    CredentialFilter().filter(record)

    assert getattr(record, STRUCTURED_FIELDS_ATTRIBUTE) == {
        "url": "bot<redacted>/getMe"
    }


def test_the_chatty_http_loggers_never_reach_a_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silence the request line at its source, not only in the formatter."""

    quiet_http_loggers()

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpx").info(REQUEST_LINE)
        logging.getLogger("httpcore").info(REQUEST_LINE)

    assert caplog.records == []
