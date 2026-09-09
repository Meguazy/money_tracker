"""Behavior tests proving no handler can emit a Bot API credential."""

from __future__ import annotations

import logging

import pytest

from money_tracker.logging_safety import configure_safe_logging, redact_credentials

TOKEN = "8435117429:AAF_-SEBbKbyAdP3FRUYCbJpk36Ypt5qXQo"
REQUEST_LINE = (
    f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getMe "HTTP/1.1 200 OK"'
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


def test_the_chatty_http_loggers_never_reach_a_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silence the request line at its source, not only in the formatter."""

    configure_safe_logging()

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpx").info(REQUEST_LINE)
        logging.getLogger("httpcore").info(REQUEST_LINE)

    assert caplog.records == []


def test_a_credential_in_any_logger_is_redacted_before_emission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Defend against the next library that logs a URL we did not expect.

    The credential arrives as a `%s` argument rather than inside the template,
    which is how a real adapter would log it and what a filter that only
    inspected the message would miss.
    """

    configure_safe_logging()

    with caplog.at_level(logging.ERROR):
        logging.getLogger("money_tracker.some.future.adapter").error(
            "call failed for %s", f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        )

    emitted = caplog.records[0].getMessage()
    assert TOKEN not in emitted
    assert "bot<redacted>/sendMessage" in emitted
