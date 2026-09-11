"""Configuration contracts for the loopback-only web process."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

WEB_HOST_ENV = "MONEY_TRACKER_WEB_HOST"
WEB_PORT_ENV = "MONEY_TRACKER_WEB_PORT"
WEB_ALLOWED_HOSTS_ENV = "MONEY_TRACKER_WEB_ALLOWED_HOSTS"
WEB_ALLOWED_ORIGINS_ENV = "MONEY_TRACKER_WEB_ALLOWED_ORIGINS"
WEB_STATIC_DIR_ENV = "MONEY_TRACKER_WEB_STATIC_DIR"


def _csv(value: str) -> tuple[str, ...]:
    """Split a comma-separated setting into non-empty unique values."""

    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return tuple(dict.fromkeys(items))


@dataclass(frozen=True, slots=True)
class WebSettings:
    """Validated settings for the local API and dashboard process.

    Attributes:
        host: IPv4 loopback listener address.
        port: TCP port used by the same-origin API and UI.
        allowed_hosts: Exact HTTP Host names admitted by the application.
        allowed_origins: Exact browser origins admitted when Origin is present.
        static_dir: Optional compiled Vite output served by FastAPI.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )
    static_dir: Path | None = None

    def __post_init__(self) -> None:
        """Reject configuration that widens the V1 browser boundary."""

        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as error:
            raise ValueError(
                f"{WEB_HOST_ENV} must be an IPv4 loopback address"
            ) from error
        if address.version != 4 or not address.is_loopback:
            raise ValueError(f"{WEB_HOST_ENV} must be an IPv4 loopback address")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"{WEB_PORT_ENV} must be between 1 and 65535")
        if not self.allowed_hosts:
            raise ValueError(f"{WEB_ALLOWED_HOSTS_ENV} must not be empty")
        for host in self.allowed_hosts:
            if host not in {"127.0.0.1", "localhost"}:
                raise ValueError(
                    f"{WEB_ALLOWED_HOSTS_ENV} may contain only loopback host names"
                )
        if not self.allowed_origins:
            raise ValueError(f"{WEB_ALLOWED_ORIGINS_ENV} must not be empty")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError(
                    f"{WEB_ALLOWED_ORIGINS_ENV} must contain exact loopback "
                    "HTTP origins"
                )
        if self.static_dir is not None and not self.static_dir.is_absolute():
            raise ValueError(f"{WEB_STATIC_DIR_ENV} must be an absolute path")

    @classmethod
    def from_env(cls) -> WebSettings:
        """Load web settings from the process environment.

        Returns:
            Validated loopback-only process settings.

        Raises:
            ValueError: If a configured port or boundary value is invalid.
        """

        host = os.getenv(WEB_HOST_ENV, "127.0.0.1").strip()
        raw_port = os.getenv(WEB_PORT_ENV, "8000").strip()
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError(f"{WEB_PORT_ENV} must be an integer") from error
        allowed_hosts = _csv(os.getenv(WEB_ALLOWED_HOSTS_ENV, "127.0.0.1,localhost"))
        default_origins = f"http://127.0.0.1:{port},http://localhost:{port}"
        allowed_origins = _csv(os.getenv(WEB_ALLOWED_ORIGINS_ENV, default_origins))
        raw_static_dir = os.getenv(WEB_STATIC_DIR_ENV, "").strip()
        default_static_dir = Path(__file__).resolve().parents[3] / "frontend" / "dist"
        return cls(
            host=host,
            port=port,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            static_dir=(Path(raw_static_dir) if raw_static_dir else default_static_dir),
        )


__all__ = [
    "WEB_ALLOWED_HOSTS_ENV",
    "WEB_ALLOWED_ORIGINS_ENV",
    "WEB_HOST_ENV",
    "WEB_PORT_ENV",
    "WEB_STATIC_DIR_ENV",
    "WebSettings",
]
