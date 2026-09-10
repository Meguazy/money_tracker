"""Tests for checked-in host service deployment contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

_UNIT_ROOT = Path("deploy/systemd")
_SERVICES = tuple(sorted(_UNIT_ROOT.glob("*.service")))
_COMMON_HARDENING = (
    "NoNewPrivileges=true",
    "UMask=0077",
    "CapabilityBoundingSet=",
    "LockPersonality=true",
    "PrivateTmp=true",
    "ProtectClock=true",
    "ProtectControlGroups=true",
    "ProtectHome=read-only",
    "ProtectKernelLogs=true",
    "ProtectKernelModules=true",
    "ProtectKernelTunables=true",
    "ProtectSystem=strict",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    "RestrictNamespaces=true",
    "RestrictRealtime=true",
    "RestrictSUIDSGID=true",
    "SystemCallArchitectures=native",
)


@pytest.mark.parametrize("unit", _SERVICES, ids=lambda path: path.stem)
def test_every_service_has_checkout_safe_docs_and_hardening(unit: Path) -> None:
    """Keep rendered paths valid and the host process sandbox uniformly strict."""

    text = unit.read_text()

    assert "Documentation=file://__MONEY_TRACKER_ROOT__/docs/" in text
    assert "WorkingDirectory=__MONEY_TRACKER_ROOT__" in text
    assert "__UV_BINARY__" not in text
    assert "ReadWritePaths=__MONEY_TRACKER_ROOT__" not in text
    for setting in _COMMON_HARDENING:
        assert setting in text


@pytest.mark.parametrize(
    ("name", "entrypoint"),
    [
        ("money-tracker-telegram.service", "money-tracker-telegram run"),
        ("money-tracker-worker.service", "money-tracker-worker run"),
    ],
)
def test_python_services_use_locked_environment_entrypoints(
    name: str, entrypoint: str
) -> None:
    """Run installed console scripts without granting UV write access at runtime."""

    text = (_UNIT_ROOT / name).read_text()

    assert "EnvironmentFile=__MONEY_TRACKER_ROOT__/.env" in text
    assert (
        f"ExecStart=__MONEY_TRACKER_ROOT__/.venv/bin/{entrypoint}" in text
    )
    assert "PrivateDevices=true" in text


def test_llama_service_retains_device_access_for_local_acceleration() -> None:
    """Harden llama.cpp without hiding the GPU devices it may need."""

    text = (_UNIT_ROOT / "money-tracker-llama.service").read_text()

    assert "PrivateDevices=true" not in text
    assert "ExecStart=__MONEY_TRACKER_ROOT__/scripts/run_llama_server.sh" in text


def test_installer_requires_the_runtime_entrypoints_without_uv_placeholder() -> None:
    """Fail installation before writing units when the locked runtime is absent."""

    text = Path("scripts/install_systemd_units.sh").read_text()

    assert ".venv/bin/money-tracker-telegram" in text
    assert ".venv/bin/money-tracker-worker" in text
    assert "UV_BINARY" not in text
