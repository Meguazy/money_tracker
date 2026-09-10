"""Tests for checked-in host service deployment contracts."""

from __future__ import annotations

import os
import subprocess
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
    assert f"ExecStart=__MONEY_TRACKER_ROOT__/.venv/bin/{entrypoint}" in text
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


def _token_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """Create isolated owner-only configuration for the credential script."""

    production = tmp_path / "credentials" / "production.token"
    test = tmp_path / "credentials" / "test.token"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE="
        f"{production}\n"
        "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE="
        f"{test}\n"
    )
    env_file.chmod(0o600)
    environment = os.environ.copy()
    environment["MONEY_TRACKER_ENV_FILE"] = str(env_file)
    return environment, production, test


def test_token_store_writes_only_the_selected_owner_file(tmp_path: Path) -> None:
    """Keep a synthetic token out of argv and store it with mode 600."""

    environment, production, test = _token_environment(tmp_path)
    token = "123456789:synthetic-test-token"

    completed = subprocess.run(
        ["bash", "scripts/store_telegram_token.sh", "test"],
        input=f"{token}\n",
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert test.read_text() == token
    assert test.stat().st_mode & 0o777 == 0o600
    assert not production.exists()
    assert token not in completed.stdout
    assert token not in completed.stderr
    assert str(test) not in completed.stdout
    assert str(test) not in completed.stderr


def test_token_store_refuses_a_symlink_target(tmp_path: Path) -> None:
    """Never replace another file through a configured credential symlink."""

    environment, _, test = _token_environment(tmp_path)
    test.parent.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    test.symlink_to(victim)

    completed = subprocess.run(
        ["bash", "scripts/store_telegram_token.sh", "test"],
        input="123456789:synthetic-test-token\n",
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert victim.read_text() == "unchanged"
    assert "unsafe Telegram credential file" in completed.stderr


def test_token_store_rejects_shared_production_and_test_paths(
    tmp_path: Path,
) -> None:
    """Keep test and production credentials physically separate."""

    environment, production, _ = _token_environment(tmp_path)
    env_file = Path(environment["MONEY_TRACKER_ENV_FILE"])
    env_file.write_text(
        "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE="
        f"{production}\n"
        "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE="
        f"{production}\n"
    )
    env_file.chmod(0o600)

    completed = subprocess.run(
        ["bash", "scripts/store_telegram_token.sh", "test"],
        input="123456789:synthetic-test-token\n",
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert not production.exists()
    assert "must use different files" in completed.stderr
