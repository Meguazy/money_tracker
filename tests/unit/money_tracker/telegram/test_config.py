"""Tests for separate owner-only Telegram credential configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from money_tracker.messaging import BotEnvironment
from money_tracker.telegram import TelegramSettings


def _environment(root: Path, *, environment: str = "test") -> dict[str, str]:
    """Return two distinct non-secret credential paths."""

    return {
        "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": environment,
        "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE": str(root / "production"),
        "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE": str(root / "test"),
    }


def test_settings_select_only_explicit_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep production and test persistence/credential scopes separate."""

    monkeypatch.setattr(os, "environ", _environment(tmp_path))
    settings = TelegramSettings.from_env()

    assert settings.environment is BotEnvironment.TEST
    assert settings.bot_key == "telegram-test"
    assert settings.token_file == tmp_path / "test"
    assert "token_file" not in repr(settings)


def test_settings_refuse_missing_or_shared_token_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject implicit selection and accidental credential reuse."""

    monkeypatch.setattr(os, "environ", {})
    with pytest.raises(RuntimeError, match="explicitly"):
        TelegramSettings.from_env()

    shared = str(tmp_path / "shared")
    monkeypatch.setattr(
        os,
        "environ",
        {
            "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": "production",
            "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE": shared,
            "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE": shared,
        },
    )
    with pytest.raises(RuntimeError, match="different"):
        TelegramSettings.from_env()


def test_token_reader_requires_owned_mode_0600_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read a credential without exposing it through settings or diagnostics."""

    environment = _environment(tmp_path)
    monkeypatch.setattr(os, "environ", environment)
    token_file = tmp_path / "test"
    token_file.write_text("runtime:" + ("x" * 40), encoding="utf-8")
    token_file.chmod(0o600)
    settings = TelegramSettings.from_env()

    assert settings.read_token().startswith("runtime:")
    token_file.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        settings.read_token()


def test_token_reader_refuses_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent final-component redirection outside the selected secret store."""

    target = tmp_path / "target"
    target.write_text("runtime:" + ("x" * 40), encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "test"
    link.symlink_to(target)
    monkeypatch.setattr(os, "environ", _environment(tmp_path))

    with pytest.raises(RuntimeError, match="regular file"):
        TelegramSettings.from_env().read_token()
