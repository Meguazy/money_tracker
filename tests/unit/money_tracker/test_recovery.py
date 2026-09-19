"""Unit tests for restored-target configuration safety."""

from pathlib import Path

import pytest

from money_tracker.recovery import RestoreIntegrityError, RestoreIntegritySettings


def test_restore_integrity_settings_require_explicit_absolute_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Load exact restore targets without defaulting a destructive scope."""

    monkeypatch.setenv(
        "MONEY_TRACKER_RESTORE_DATABASE_URL",
        "postgresql://user:secret@127.0.0.1:5433/money_tracker_test",
    )
    monkeypatch.setenv("MONEY_TRACKER_RESTORE_EXPECTED_DATABASE", "money_tracker_test")
    monkeypatch.setenv("MONEY_TRACKER_RESTORE_RECEIPT_ROOT", str(tmp_path))

    settings = RestoreIntegritySettings.from_env()

    assert settings.expected_database == "money_tracker_test"
    assert settings.receipt_root == tmp_path

    monkeypatch.setenv("MONEY_TRACKER_RESTORE_RECEIPT_ROOT", "relative")
    with pytest.raises(RestoreIntegrityError) as failure:
        RestoreIntegritySettings.from_env()
    assert failure.value.reason_code == "receipt_root_not_absolute"


def test_restore_integrity_settings_reject_missing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Name a stable reason without retaining private configuration values."""

    monkeypatch.delenv("MONEY_TRACKER_RESTORE_DATABASE_URL", raising=False)

    with pytest.raises(RestoreIntegrityError) as failure:
        RestoreIntegritySettings.from_env()

    assert failure.value.reason_code == "restore_setting_missing"
