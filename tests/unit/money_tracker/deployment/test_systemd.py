"""Tests for checked-in host service deployment contracts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_UNIT_ROOT = Path("deploy/systemd")
_SERVICES = tuple(sorted(_UNIT_ROOT.glob("*.service")))
_COMMON_HARDENING = (
    "NoNewPrivileges=true",
    "UMask=0077",
    "LockPersonality=true",
    "PrivateTmp=true",
    "ProtectControlGroups=true",
    "ProtectHome=read-only",
    "ProtectKernelTunables=true",
    "ProtectSystem=strict",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    "RestrictNamespaces=true",
    "RestrictRealtime=true",
    "RestrictSUIDSGID=true",
    "SystemCallArchitectures=native",
)
_UNSUPPORTED_BY_USER_MANAGER = (
    "CapabilityBoundingSet=",
    "PrivateDevices=",
    "ProtectClock=",
    "ProtectKernelLogs=",
    "ProtectKernelModules=",
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


@pytest.mark.parametrize("unit", _SERVICES, ids=lambda path: path.stem)
def test_no_service_declares_hardening_a_user_manager_cannot_apply(
    unit: Path,
) -> None:
    """Keep every unit startable by the unprivileged manager that owns it.

    Dropping bounding-set capabilities requires `CAP_SETPCAP`. A user manager
    has none, so each directive below stops the unit with `218/CAPABILITIES`
    before `ExecStart` ever runs. `systemd-analyze verify` and `security` score
    configuration rather than execution and report such a unit as unusually
    well hardened, which is how these reached the repository in the first
    place.
    """

    directives = tuple(
        line.strip()
        for line in unit.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )

    for setting in _UNSUPPORTED_BY_USER_MANAGER:
        assert not [line for line in directives if line.startswith(setting)]


@pytest.mark.parametrize(
    ("name", "entrypoint"),
    [
        ("money-tracker-api.service", "money-tracker-api run"),
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


def test_llama_service_retains_device_access_for_local_acceleration() -> None:
    """Keep the GPU nodes visible to the process that needs them.

    Device isolation is unavailable to every unit here, so this states the
    intent that survives: llama.cpp must never acquire it even if a system
    unit later makes it possible for the other two.
    """

    text = (_UNIT_ROOT / "money-tracker-llama.service").read_text()

    assert "PrivateDevices=true" not in text
    assert "ExecStart=__MONEY_TRACKER_ROOT__/scripts/run_llama_server.sh" in text


def test_installer_requires_the_runtime_entrypoints_without_uv_placeholder() -> None:
    """Fail installation before writing units when the locked runtime is absent."""

    text = Path("scripts/install_systemd_units.sh").read_text()

    assert ".venv/bin/money-tracker-api" in text
    assert ".venv/bin/money-tracker-telegram" in text
    assert ".venv/bin/money-tracker-worker" in text
    assert "UV_BINARY" not in text


def test_installer_bootstraps_gateway_before_requiring_completed_pairing(
    tmp_path: Path,
) -> None:
    """Keep fresh-host pairing possible before the worker is enabled."""

    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "deploy").mkdir()
    (checkout / ".venv" / "bin").mkdir(parents=True)
    shutil.copy2("scripts/install_systemd_units.sh", checkout / "scripts")
    shutil.copytree(_UNIT_ROOT, checkout / "deploy" / "systemd")
    (checkout / ".env").write_text("SAFE_TEST_SETTING=1\n")
    (checkout / "frontend" / "dist" / "assets").mkdir(parents=True)
    (checkout / "frontend" / "dist" / "index.html").write_text("dashboard")
    for entrypoint in (
        "money-tracker-api",
        "money-tracker-telegram",
        "money-tracker-worker",
    ):
        path = checkout / ".venv" / "bin" / entrypoint
        path.write_text("#!/usr/bin/env bash\nexit 0\n")
        path.chmod(0o700)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/usr/bin/env bash\nexit 0\n")
    systemctl.chmod(0o700)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")

    completed = subprocess.run(
        ["bash", str(checkout / "scripts" / "install_systemd_units.sh")],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    create = completed.stdout.index("money-tracker-telegram pair")
    gateway = completed.stdout.index(
        "systemctl --user start money-tracker-telegram.service"
    )
    worker = completed.stdout.index(
        "systemctl --user enable --now money-tracker-worker.service"
    )
    api = completed.stdout.index(
        "systemctl --user enable --now money-tracker-api.service"
    )
    assert create < gateway < worker
    assert worker < api
    rendered = (
        tmp_path / "config" / "systemd" / "user" / "money-tracker-worker.service"
    ).read_text()
    assert str(checkout) in rendered
    assert "__MONEY_TRACKER_ROOT__" not in rendered


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


@pytest.mark.parametrize(
    ("name", "prevented"),
    [
        ("money-tracker-telegram.service", "1"),
        ("money-tracker-worker.service", "1"),
        ("money-tracker-llama.service", "1 2"),
    ],
)
def test_each_unit_refuses_to_restart_what_an_operator_must_fix(
    name: str, prevented: str
) -> None:
    """Stop visibly instead of looping on a failure a restart cannot clear.

    The Python entry points own their exit codes and use 1 for that. llama.cpp
    does not: it exits 1 when its port is taken, which restarting never frees.
    Leaving that out produced 54 restarts in a row against an occupied 8080.
    """

    text = (_UNIT_ROOT / name).read_text()

    assert f"RestartPreventExitStatus={prevented}\n" in text


def test_llama_unit_finds_a_user_installed_binary_at_boot() -> None:
    """Resolve `llama-server` without a login session to inherit PATH from.

    `LLAMA_SERVER_BIN` defaults to a bare name, which a login shell resolves
    through `~/.local/bin`. A lingering user manager starts at boot before any
    login session exists, so that directory is absent from its PATH and the
    launcher cannot find the binary. Starting the service by hand always
    worked, which is why only a real reboot exposed it.
    """

    text = (_UNIT_ROOT / "money-tracker-llama.service").read_text()

    path = next(
        line for line in text.splitlines() if line.startswith("Environment=PATH=")
    )
    assert "%h/.local/bin" in path
    assert "/usr/bin" in path


def test_llama_launcher_refuses_a_silent_cpu_fallback(tmp_path: Path) -> None:
    """Fail loudly where llama.cpp would quietly run thirty times slower.

    CUDA initialization failure is a warning llama.cpp keeps running past, so
    the unit stays active, the health endpoint answers, and no queue grows
    while every inference takes seventy seconds instead of two. The first real
    reboot lost that race by 400 milliseconds, and nothing downstream noticed.
    """

    model = tmp_path / "model.gguf"
    model.write_text("not a real model")
    stub = tmp_path / "bin"
    stub.mkdir()
    driver = stub / "nvidia-smi"
    driver.write_text("#!/bin/sh\nexit 1\n")
    driver.chmod(0o700)
    environment = {
        "PATH": f"{stub}:/usr/bin:/bin",
        "MODEL_PATH": str(model),
        "LLAMA_SERVER_BIN": "/bin/echo",
        "LLAMA_GPU_WAIT_SECONDS": "1",
    }

    refused = subprocess.run(
        ["bash", "scripts/run_llama_server.sh"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
    )
    accepted = subprocess.run(
        ["bash", "scripts/run_llama_server.sh"],
        check=False,
        capture_output=True,
        env={**environment, "LLAMA_REQUIRE_GPU": "0"},
        text=True,
        timeout=30,
    )

    assert refused.returncode == 2
    assert "No usable GPU" in refused.stderr
    assert accepted.returncode == 0
