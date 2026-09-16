"""Redaction primitives that keep a credential out of every log record.

The Telegram Bot API puts the token in the request path, so any library that
logs a URL logs the credential. `httpx` does exactly that at INFO level, which
a plain `logging.basicConfig(level=INFO)` enables for every logger in the
process. The result reached a terminal, a scrollback buffer, and the journal
before anyone noticed.

Two defences, because either alone is one mistake away from failing again.
Chatty HTTP loggers are raised to WARNING so the request line is never emitted,
and a filter on the root handler redacts anything shaped like a bot token from
every record regardless of which library produced it.

Tracing SDKs are a third source. The Langfuse callback logs complete chain
inputs at DEBUG, and the OpenTelemetry exporter interpolates the endpoint URL
and the transport error into its messages. Their loggers are held above DEBUG,
and every record they emit is rewritten into a stable, content-free event
before any handler formats it, so the journal learns that tracing degraded
without learning what was being traced.

This module owns only the redaction contract. Process-wide configuration lives
in `money_tracker.structured_logging`, which composes these defences with the
structured formatter; the dependency runs in that direction so redaction stays
usable by anything that logs, including code that never builds a record itself.
"""

from __future__ import annotations

import logging
import re
from typing import Final

HTTP_LOGGERS: Final = ("httpx", "httpcore", "urllib3")
TRACING_SDK_LOGGERS: Final = ("langfuse", "opentelemetry")
STRUCTURED_FIELDS_ATTRIBUTE: Final = "money_tracker_fields"
_SDK_RECORD_REWRITTEN: Final = "money_tracker_sdk_record_rewritten"
_REDACTED = "bot<redacted>"
# A Bot API token is a numeric bot id, a colon, and a long opaque secret. The
# `bot` prefix is how it appears in a URL path; the bare form catches a token
# logged on its own.
_URL_TOKEN = re.compile(r"bot\d{5,}:[A-Za-z0-9_\-]{20,}")
_BARE_TOKEN = re.compile(r"\b\d{5,}:[A-Za-z0-9_\-]{20,}")


def redact_credentials(text: str) -> str:
    """Remove anything shaped like a Bot API token from one string.

    Args:
        text: Message that may embed a credential.

    Returns:
        The text with every token-shaped run replaced.
    """

    return _BARE_TOKEN.sub("<redacted>", _URL_TOKEN.sub(_REDACTED, text))


def redact_value(value: object) -> object:
    """Redact one structured field value without changing its type otherwise.

    Args:
        value: Field value supplied by a caller or bound into log context.

    Returns:
        The redacted string when the value is text, the value unchanged
        otherwise. Non-string values are returned as they are because a token
        is a string everywhere it appears, and rewriting other types would make
        structured fields lie about what the code observed.
    """

    return redact_credentials(value) if isinstance(value, str) else value


class CredentialFilter(logging.Filter):
    """Redact token-shaped runs from a record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the message, arguments, and fields in place, and keep it.

        Formatting happens in the handler, so both the template and its
        arguments have to be cleaned: a token arriving as `%s` would otherwise
        survive a filter that only looked at `record.msg`. Structured fields
        are cleaned for the same reason, since a formatter serializes them
        after every filter has run.
        """

        if isinstance(record.msg, str):
            record.msg = redact_credentials(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_value(argument) for argument in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_value(value) for key, value in record.args.items()
            }
        fields = getattr(record, STRUCTURED_FIELDS_ATTRIBUTE, None)
        if isinstance(fields, dict):
            setattr(
                record,
                STRUCTURED_FIELDS_ATTRIBUTE,
                {key: redact_value(value) for key, value in fields.items()},
            )
        return True


def quiet_http_loggers() -> None:
    """Raise chatty HTTP client loggers above the level that logs a URL."""

    for name in HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def quiet_tracing_sdk_loggers(level: int = logging.WARNING) -> None:
    """Hold tracing SDK loggers at WARNING or above so payload records never form.

    Args:
        level: The process's configured level. The SDK loggers never go below
            WARNING, and never below a stricter process level either.
    """

    for name in TRACING_SDK_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, level))


class TracingSdkRecordFilter(logging.Filter):
    """Replace a tracing SDK record's text with a stable content-free event.

    A logger's own filters do not apply to records from its children, so this
    runs on handlers and selects records by logger name. The message, its
    arguments, and any traceback are dropped because each can carry a URL or a
    traced payload; what survives is which SDK component reported, at what
    level, and the exception class when there was one.

    An export failure is recorded at WARNING whatever level the exporter chose:
    the trace store is optional, the application already carried on without
    it, and that is a degraded outcome the system handled itself.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite a tracing SDK record in place and keep it."""

        if not record.name.startswith(TRACING_SDK_LOGGERS) or getattr(
            record, _SDK_RECORD_REWRITTEN, False
        ):
            # Every handler carries this filter and they share one record, so a
            # record rewritten by the first handler must survive the next.
            return True
        exception_type = (
            record.exc_info[0].__name__
            if record.exc_info and record.exc_info[0] is not None
            else None
        )
        if record.name.startswith("opentelemetry"):
            record.msg = "tracing.export.degraded"
            record.levelno = min(record.levelno, logging.WARNING)
            record.levelname = logging.getLevelName(record.levelno)
        else:
            record.msg = "tracing.sdk.reported"
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        fields = {"sdk_logger": record.name}
        if exception_type is not None:
            fields["exception_type"] = exception_type
        setattr(record, STRUCTURED_FIELDS_ATTRIBUTE, fields)
        setattr(record, _SDK_RECORD_REWRITTEN, True)
        return True


__all__ = [
    "HTTP_LOGGERS",
    "STRUCTURED_FIELDS_ATTRIBUTE",
    "TRACING_SDK_LOGGERS",
    "CredentialFilter",
    "TracingSdkRecordFilter",
    "quiet_http_loggers",
    "quiet_tracing_sdk_loggers",
    "redact_credentials",
    "redact_value",
]
