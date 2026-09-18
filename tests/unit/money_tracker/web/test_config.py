"""Tests for the closed loopback web configuration boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from money_tracker.web.config import (
    WEB_ALLOWED_HOSTS_ENV,
    WEB_ALLOWED_ORIGINS_ENV,
    WEB_HOST_ENV,
    WEB_PORT_ENV,
    WEB_STATIC_DIR_ENV,
    WebSettings,
)


def test_settings_load_an_exact_loopback_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derive the browser allowlist from the configured local port."""

    # The defaults are under test, so an ambient `.env` must not supply them.
    for name in (WEB_HOST_ENV, WEB_ALLOWED_HOSTS_ENV, WEB_ALLOWED_ORIGINS_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(WEB_PORT_ENV, "8765")
    monkeypatch.setenv(WEB_STATIC_DIR_ENV, "/tmp/money-tracker-ui")

    settings = WebSettings.from_env()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.allowed_hosts == ("127.0.0.1", "localhost")
    assert settings.allowed_origins == (
        "http://127.0.0.1:8765",
        "http://localhost:8765",
    )
    assert settings.static_dir == Path("/tmp/money-tracker-ui")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.4", "::1", "example.test"])
def test_non_ipv4_loopback_listeners_are_rejected(host: str) -> None:
    """Keep the V1 control plane off LAN and wildcard interfaces."""

    with pytest.raises(ValueError, match="IPv4 loopback"):
        WebSettings(host=host)


def test_non_loopback_host_headers_are_rejected() -> None:
    """Prevent configuration from weakening DNS-rebinding protection."""

    with pytest.raises(ValueError, match="only loopback"):
        WebSettings(allowed_hosts=("money.example",))


@pytest.mark.parametrize(
    "origin",
    ["https://localhost:8000", "http://192.168.1.4:8000", "http://localhost/x"],
)
def test_non_exact_local_origins_are_rejected(origin: str) -> None:
    """Accept only exact plain-HTTP origins served by this local process."""

    with pytest.raises(ValueError, match="exact loopback"):
        WebSettings(allowed_origins=(origin,))


def test_environment_rejects_an_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail configuration before the server attempts to bind."""

    monkeypatch.setenv(WEB_HOST_ENV, "127.0.0.1")
    monkeypatch.setenv(WEB_PORT_ENV, "not-a-port")
    monkeypatch.setenv(WEB_ALLOWED_HOSTS_ENV, "localhost")
    monkeypatch.setenv(WEB_ALLOWED_ORIGINS_ENV, "http://localhost:8000")

    with pytest.raises(ValueError, match="must be an integer"):
        WebSettings.from_env()
