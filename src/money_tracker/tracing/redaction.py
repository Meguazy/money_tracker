"""Last-line removal of what AD-021 keeps out of the trace store.

Full-fidelity traces carry owner messages, prompts, completions, and grounded
figures on purpose. They still never carry a credential, a token-bearing or
database URL, a receipt-archive path, or receipt bytes. Components keep those
out by construction: no model-facing value holds one, and the tracing port
accepts no exception text. This redactor exists for the case construction
misses, such as an owner who pastes a token into a message or a graph state
that one day gains a field it should not have.

Like `money_tracker.logging_safety`, it is a defence rather than a licence. It
recognizes shapes and the literal values this process was configured with; it
cannot recognize a secret it was never told about.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

from money_tracker.logging_safety import redact_credentials

SECRET_ENVIRONMENT: Final = (
    "LANGFUSE_SECRET_KEY",
    "LLM_API_KEY",
    "MONEY_TRACKER_DATABASE_URL",
    "MONEY_TRACKER_TEST_DATABASE_URL",
    "MONEY_TRACKER_RESTORE_DATABASE_URL",
)
"""Settings whose literal values must never appear in a trace.

The Langfuse public key is deliberately absent. It identifies the project, the
SDK stamps it on every span as an instrumentation-scope attribute so the
exporter can route spans, and it grants nothing without the secret key.
"""

PRIVATE_ROOT_ENVIRONMENT: Final = (
    "MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT",
    "MONEY_TRACKER_RESTORE_RECEIPT_ROOT",
)
"""Settings naming directories whose paths must never appear in a trace."""

MINIMUM_SECRET_LENGTH: Final = 12
"""Shorter configured values are not treated as literal secrets.

A development default such as `local` also occurs in ordinary text, and
replacing every occurrence would corrupt the trace without protecting anything.
"""

_DATABASE_URL: Final = re.compile(r"\bpostgres(?:ql)?(?:\+[a-z0-9]+)?://\S+", re.I)
_CREDENTIAL_URL: Final = re.compile(
    r"\b[a-z][a-z0-9+.\-]*://[^\s/@:]+:[^\s/@]*@\S*", re.I
)
_TELEGRAM_URL: Final = re.compile(r"\bhttps?://api\.telegram\.org/\S*", re.I)


class TraceRedactor:
    """Rewrite forbidden values inside an observation payload.

    Instances are callable with the keyword signature the Langfuse SDK expects
    of a mask function, so the adapter can hand one over without a wrapper.
    """

    def __init__(
        self,
        *,
        secrets: Iterable[str] = (),
        private_roots: Iterable[str] = (),
    ) -> None:
        """Bind the literal secrets and private directories to remove.

        Args:
            secrets: Configured credential values. Values shorter than
                `MINIMUM_SECRET_LENGTH` are ignored.
            private_roots: Absolute directories whose paths, and anything
                beneath them, are replaced.
        """

        literal = sorted(
            {value for value in secrets if len(value) >= MINIMUM_SECRET_LENGTH},
            key=len,
            reverse=True,
        )
        self._secrets = tuple(literal)
        roots = sorted(
            {root.rstrip("/") for root in private_roots if root.strip("/")},
            key=len,
            reverse=True,
        )
        self._roots = tuple(
            re.compile(re.escape(root) + r"(?:/[^\s\"']*)?") for root in roots
        )

    @classmethod
    def from_env(cls) -> TraceRedactor:
        """Build a redactor from the credentials this process was given.

        Returns:
            A redactor knowing every configured secret and private root, plus
            the password component of each configured database URL.
        """

        secrets: list[str] = []
        for name in SECRET_ENVIRONMENT:
            value = os.getenv(name, "").strip()
            if value:
                secrets.append(value)
                secrets.extend(_url_password(value))
        roots = [
            value
            for name in PRIVATE_ROOT_ENVIRONMENT
            if (value := os.getenv(name, "").strip())
        ]
        roots.extend(os.path.realpath(root) for root in list(roots))
        return cls(secrets=secrets, private_roots=roots)

    def __call__(self, *, data: Any, **kwargs: Any) -> Any:
        """Return a copy of `data` with every forbidden value replaced.

        Args:
            data: Observation input, output, or metadata.
            **kwargs: Additional SDK arguments, ignored.

        Returns:
            Structurally identical data with forbidden strings and all raw
            bytes replaced by descriptive placeholders.
        """

        return self.redact(data)

    def redact(self, value: Any) -> Any:
        """Recursively redact one JSON-like value.

        Args:
            value: String, bytes, mapping, sequence, or scalar.

        Returns:
            The redacted value; scalars other than text are returned unchanged.
        """

        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, bytes | bytearray | memoryview):
            return f"<bytes length={len(value)}>"
        if isinstance(value, Mapping):
            return {
                self._redact_text(key) if isinstance(key, str) else key: self.redact(
                    item
                )
                for key, item in value.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [self.redact(item) for item in value]
        return value

    def _redact_text(self, text: str) -> str:
        """Remove secrets, forbidden URLs, and private paths from one string."""

        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "<redacted-secret>")
        text = _DATABASE_URL.sub("<redacted-database-url>", text)
        text = _CREDENTIAL_URL.sub("<redacted-credential-url>", text)
        text = _TELEGRAM_URL.sub("<redacted-telegram-url>", text)
        text = redact_credentials(text)
        for root in self._roots:
            text = root.sub("<private-path>", text)
        return text


def _url_password(url: str) -> list[str]:
    """Return the password component of a URL-shaped setting, if it has one."""

    match = re.match(r"^[a-z][a-z0-9+.\-]*://[^\s/@:]+:([^\s/@]+)@", url, re.I)
    return [match.group(1)] if match else []


__all__ = [
    "MINIMUM_SECRET_LENGTH",
    "PRIVATE_ROOT_ENVIRONMENT",
    "SECRET_ENVIRONMENT",
    "TraceRedactor",
]
