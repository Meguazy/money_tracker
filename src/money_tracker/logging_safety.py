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
STRUCTURED_FIELDS_ATTRIBUTE: Final = "money_tracker_fields"
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


__all__ = [
    "HTTP_LOGGERS",
    "STRUCTURED_FIELDS_ATTRIBUTE",
    "CredentialFilter",
    "quiet_http_loggers",
    "redact_credentials",
    "redact_value",
]
