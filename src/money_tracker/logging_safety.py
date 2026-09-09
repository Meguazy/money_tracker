"""Logging configuration that cannot print a Bot API credential.

The Telegram Bot API puts the token in the request path, so any library that
logs a URL logs the credential. `httpx` does exactly that at INFO level, which
a plain `logging.basicConfig(level=INFO)` enables for every logger in the
process. The result reached a terminal, a scrollback buffer, and the journal
before anyone noticed.

Two defences, because either alone is one mistake away from failing again.
Chatty HTTP loggers are raised to WARNING so the request line is never emitted,
and a filter on the root handler redacts anything shaped like a bot token from
every record regardless of which library produced it.
"""

from __future__ import annotations

import logging
import re

_HTTP_LOGGERS = ("httpx", "httpcore", "urllib3")
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


class _CredentialFilter(logging.Filter):
    """Redact token-shaped runs from a record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the message and arguments in place, and keep the record.

        Formatting happens in the handler, so both the template and its
        arguments have to be cleaned: a token arriving as `%s` would otherwise
        survive a filter that only looked at `record.msg`.
        """

        if isinstance(record.msg, str):
            record.msg = redact_credentials(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_credentials(argument) if isinstance(argument, str) else argument
                for argument in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_credentials(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def configure_safe_logging(level: int = logging.INFO) -> None:
    """Configure process logging so no handler can emit a credential.

    Args:
        level: Level for the application's own loggers.
    """

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    credential_filter = _CredentialFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(credential_filter)
    for name in _HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


__all__ = ["configure_safe_logging", "redact_credentials"]
