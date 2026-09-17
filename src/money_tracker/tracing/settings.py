"""Explicit configuration for the optional trace store.

Tracing is off unless the operator switches it on. The project keys that
Langfuse initialization already writes into `.env` are not enough on their
own, because every host service reads that file: a process that silently began
exporting complete prompts the moment it was restarted would widen what leaves
it without anyone deciding so.

The endpoint must also be local. AD-021 accepts complete prompts, completions,
and decision context in traces only because the store sits on the same
workstation as the authoritative database. The SDK's own default endpoint is a
cloud service, so an absent or remote base URL is refused rather than
defaulted.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

TRACING_ENABLED_ENV: Final = "MONEY_TRACKER_TRACING_ENABLED"
TRACING_EXPORT_TIMEOUT_ENV: Final = "MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS"
LANGFUSE_BASE_URL_ENV: Final = "LANGFUSE_BASE_URL"
LANGFUSE_PUBLIC_KEY_ENV: Final = "LANGFUSE_PUBLIC_KEY"
LANGFUSE_SECRET_KEY_ENV: Final = "LANGFUSE_SECRET_KEY"
LANGFUSE_ENVIRONMENT_ENV: Final = "LANGFUSE_TRACING_ENVIRONMENT"
DEFAULT_EXPORT_TIMEOUT_SECONDS: Final = 2
DEFAULT_ENVIRONMENT: Final = "development"
_LOOPBACK_NAMES: Final = frozenset({"localhost"})
_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"", "0", "false", "no", "off"})


class TracingConfigurationError(ValueError):
    """Tracing was requested with configuration that cannot be honored.

    The message names the setting and the rule it broke, never its value, so it
    is safe to record.

    Attributes:
        reason_code: Stable machine-readable reason for the journal.
    """

    def __init__(self, reason_code: str, message: str) -> None:
        """Bind a stable reason to a value-free explanation.

        Args:
            reason_code: Stable machine-readable reason.
            message: Human-readable explanation naming settings, not values.
        """

        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class TracingSettings:
    """Validated trace-store configuration for one process.

    Attributes:
        enabled: Whether the operator switched tracing on.
        base_url: Loopback Langfuse base URL, empty when disabled.
        public_key: Langfuse project public key, empty when disabled.
        secret_key: Langfuse project secret key, empty when disabled.
        environment: Langfuse environment label separating trace populations.
        export_timeout_seconds: Upper bound on one export attempt and on the
            final flush, which is what a stopped or slow store can cost.
    """

    enabled: bool
    base_url: str = ""
    public_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    environment: str = DEFAULT_ENVIRONMENT
    export_timeout_seconds: int = DEFAULT_EXPORT_TIMEOUT_SECONDS

    @classmethod
    def disabled(cls) -> TracingSettings:
        """Return settings under which nothing is traced."""

        return cls(enabled=False)

    @classmethod
    def from_env(cls) -> TracingSettings:
        """Read the switch and, when it is on, the local store configuration.

        Returns:
            Disabled settings unless `MONEY_TRACKER_TRACING_ENABLED` is true;
            otherwise complete, validated loopback settings.

        Raises:
            TracingConfigurationError: If the switch is unreadable, or tracing
                is on and a key is missing, the endpoint is not loopback, or the
                timeout is not a positive integer.
        """

        switch = os.getenv(TRACING_ENABLED_ENV, "").strip().lower()
        if switch in _FALSE:
            return cls.disabled()
        if switch not in _TRUE:
            raise TracingConfigurationError(
                "invalid_switch", f"{TRACING_ENABLED_ENV} must be true or false"
            )
        return cls._store_from_env()

    @classmethod
    def for_command(cls, *, environment: str) -> TracingSettings:
        """Read the local store configuration for an explicit operator command.

        Publishing evaluation evidence and planning, exporting, or deleting
        traces are each requested by their own command, which is the explicit
        switch, so the runtime tracing switch is not consulted. Every other rule
        is the same: both keys are required, the endpoint must be loopback, and
        the SDK's cloud default is never used.

        Args:
            environment: Langfuse environment the command works in.

        Returns:
            Complete, validated loopback settings.

        Raises:
            TracingConfigurationError: If a key is missing, the endpoint is not
                loopback, or the timeout is not a positive integer.
        """

        settings = cls._store_from_env()
        return cls(
            enabled=True,
            base_url=settings.base_url,
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            environment=environment,
            export_timeout_seconds=settings.export_timeout_seconds,
        )

    @classmethod
    def _store_from_env(cls) -> TracingSettings:
        """Read and validate the store keys, endpoint, and export bound.

        Returns:
            Enabled settings for the configured local store.

        Raises:
            TracingConfigurationError: If a key is missing, the endpoint is not
                loopback, or the timeout is not a positive integer.
        """

        public_key = os.getenv(LANGFUSE_PUBLIC_KEY_ENV, "").strip()
        secret_key = os.getenv(LANGFUSE_SECRET_KEY_ENV, "").strip()
        if not public_key or not secret_key:
            raise TracingConfigurationError(
                "missing_project_keys",
                f"{LANGFUSE_PUBLIC_KEY_ENV} and {LANGFUSE_SECRET_KEY_ENV} are required "
                "when tracing is enabled",
            )
        base_url = os.getenv(LANGFUSE_BASE_URL_ENV, "").strip().rstrip("/")
        if not _is_loopback_http(base_url):
            raise TracingConfigurationError(
                "non_loopback_endpoint",
                f"{LANGFUSE_BASE_URL_ENV} must be an http(s) URL on a loopback host",
            )
        raw_timeout = os.getenv(
            TRACING_EXPORT_TIMEOUT_ENV, str(DEFAULT_EXPORT_TIMEOUT_SECONDS)
        ).strip()
        if not raw_timeout.isdigit() or int(raw_timeout) < 1:
            raise TracingConfigurationError(
                "invalid_export_timeout",
                f"{TRACING_EXPORT_TIMEOUT_ENV} must be a positive whole number",
            )
        return cls(
            enabled=True,
            base_url=base_url,
            public_key=public_key,
            secret_key=secret_key,
            environment=os.getenv(LANGFUSE_ENVIRONMENT_ENV, "").strip()
            or DEFAULT_ENVIRONMENT,
            export_timeout_seconds=int(raw_timeout),
        )


def _is_loopback_http(url: str) -> bool:
    """Tell whether a URL names an HTTP endpoint on this workstation only.

    Args:
        url: Candidate base URL.

    Returns:
        True for an http or https URL without credentials whose host is
        `localhost` or a loopback address.
    """

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        return False
    host = parts.hostname or ""
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


__all__ = [
    "DEFAULT_EXPORT_TIMEOUT_SECONDS",
    "TRACING_ENABLED_ENV",
    "TRACING_EXPORT_TIMEOUT_ENV",
    "TracingConfigurationError",
    "TracingSettings",
]
