"""Unit tests for authoritative backup safety contracts."""

from __future__ import annotations

import json
import stat
from importlib.metadata import version
from pathlib import Path

import pytest

from money_tracker.backup import (
    RETENTION_ARGUMENTS,
    AuthoritativeBackupService,
    BackupError,
    BackupSettings,
    _libpq_environment,
    _prepare_private_directory,
    _require_empty_restore_target,
    _require_private_file,
    _restored_absolute,
)


def _settings(tmp_path: Path) -> BackupSettings:
    """Build settings sufficient for local filesystem-only unit tests."""

    return BackupSettings(
        database_url="postgresql://user:secret@127.0.0.1:5433/money_tracker",
        expected_database="money_tracker",
        receipt_archive_root=tmp_path / "receipts",
        config_file=tmp_path / "runtime.env",
        restic_repository=tmp_path / "repository",
        restic_password_file=tmp_path / "password",
        lock_file=tmp_path / "locks" / "backup.lock",
        staging_parent=tmp_path / "staging",
        restic_binary=Path("/bin/true"),
        pg_dump_binary=Path("/bin/true"),
        pg_restore_binary=Path("/bin/true"),
    )


def _manifest() -> dict[str, object]:
    """Return one valid version-one manifest payload."""

    return {
        "format_version": 1,
        "application_version": version("money-tracker"),
        "database_name": "money_tracker",
        "alembic_revision": "0002_finance_domain",
        "database_dump_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "receipt_archive_root": "/private/receipts",
        "config_file": "/private/runtime.env",
        "receipt_hashes": ["c" * 64],
    }


def test_settings_load_explicit_absolute_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Map every documented setting without expanding caller-controlled paths."""

    values = {
        "MONEY_TRACKER_DATABASE_URL": "postgresql://user:p%40ss@db:5440/finance",
        "MONEY_TRACKER_BACKUP_EXPECTED_DATABASE": "finance",
        "MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT": str(tmp_path / "receipts"),
        "MONEY_TRACKER_BACKUP_CONFIG_FILE": str(tmp_path / "runtime.env"),
        "MONEY_TRACKER_RESTIC_REPOSITORY": str(tmp_path / "repository"),
        "MONEY_TRACKER_RESTIC_PASSWORD_FILE": str(tmp_path / "password"),
        "MONEY_TRACKER_BACKUP_LOCK_FILE": str(tmp_path / "backup.lock"),
        "MONEY_TRACKER_BACKUP_STAGING_PARENT": str(tmp_path / "staging"),
        "MONEY_TRACKER_RESTIC_BINARY": "/opt/restic",
        "MONEY_TRACKER_PG_DUMP_BINARY": "/opt/pg_dump",
        "MONEY_TRACKER_PG_RESTORE_BINARY": "/opt/pg_restore",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = BackupSettings.from_env()
    environment, database_name = _libpq_environment(settings.database_url)

    assert settings.expected_database == "finance"
    assert settings.restic_binary == Path("/opt/restic")
    assert database_name == "finance"
    assert environment["PGHOST"] == "db"
    assert environment["PGPORT"] == "5440"
    assert environment["PGPASSWORD"] == "p@ss"


def test_settings_reject_missing_and_relative_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail before work when required configuration is absent or ambiguous."""

    monkeypatch.delenv("MONEY_TRACKER_DATABASE_URL", raising=False)
    with pytest.raises(BackupError, match="DATABASE_URL is required"):
        BackupSettings.from_env()

    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", "postgresql://u@db/finance")
    monkeypatch.setenv("MONEY_TRACKER_BACKUP_EXPECTED_DATABASE", "finance")
    monkeypatch.setenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", "relative")
    with pytest.raises(BackupError, match="must be absolute"):
        BackupSettings.from_env()


@pytest.mark.parametrize("mode", [0o604, 0o640, 0o644])
def test_secret_inputs_require_owner_only_regular_files(
    tmp_path: Path,
    mode: int,
) -> None:
    """Reject any configuration or recovery-secret permissions visible to peers."""

    secret = tmp_path / "secret"
    secret.write_text("private\n", encoding="utf-8")
    secret.chmod(mode)

    with pytest.raises(BackupError, match="private regular file"):
        _require_private_file(secret, "secret")


def test_secret_inputs_reject_symlinks(tmp_path: Path) -> None:
    """Do not follow a final symlink when checking a secret-bearing file."""

    secret = tmp_path / "secret"
    secret.write_text("private\n", encoding="utf-8")
    secret.chmod(0o600)
    link = tmp_path / "secret-link"
    link.symlink_to(secret)

    with pytest.raises(BackupError, match="private regular file"):
        _require_private_file(link, "secret")


def test_private_directory_creation_and_symlink_refusal(tmp_path: Path) -> None:
    """Create mode-0700 staging while refusing a redirected final directory."""

    staging = tmp_path / "staging"
    _prepare_private_directory(staging, "staging")
    assert stat.S_IMODE(staging.stat().st_mode) == 0o700

    target = tmp_path / "external"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(BackupError, match="not a symlink or file"):
        _prepare_private_directory(link, "staging")


def test_restore_target_accepts_only_absent_or_empty_real_directory(
    tmp_path: Path,
) -> None:
    """Reject non-empty, file, and symlink targets before database mutation."""

    absent = tmp_path / "absent"
    _require_empty_restore_target(absent)
    empty = tmp_path / "empty"
    empty.mkdir()
    _require_empty_restore_target(empty)

    (empty / "owned").write_text("data", encoding="utf-8")
    with pytest.raises(BackupError, match="must be empty"):
        _require_empty_restore_target(empty)
    regular = tmp_path / "regular"
    regular.write_text("data", encoding="utf-8")
    with pytest.raises(BackupError, match="real directory"):
        _require_empty_restore_target(regular)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(BackupError, match="real directory"):
        _require_empty_restore_target(link)


def test_restored_absolute_rejects_relative_escape_and_symlink(
    tmp_path: Path,
) -> None:
    """Keep manifest-selected sources beneath real Restic staging paths."""

    restore_root = tmp_path / "restore"
    (restore_root / "private").mkdir(parents=True)
    assert _restored_absolute(restore_root, Path("/private/data")) == (
        restore_root / "private" / "data"
    )
    with pytest.raises(BackupError, match="must be absolute"):
        _restored_absolute(restore_root, Path("relative/data"))
    with pytest.raises(BackupError, match="escapes"):
        _restored_absolute(restore_root, Path("/../../outside"))

    external = tmp_path / "external"
    external.mkdir()
    (restore_root / "private" / "link").symlink_to(external, target_is_directory=True)
    with pytest.raises(BackupError, match="contains a symlink"):
        _restored_absolute(restore_root, Path("/private/link/data"))


def test_manifest_loads_only_strict_compatible_integrity_values(tmp_path: Path) -> None:
    """Accept one exact manifest and preserve receipt hashes as an immutable tuple."""

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    manifest = AuthoritativeBackupService._load_manifest(path)

    assert manifest.database_name == "money_tracker"
    assert manifest.receipt_hashes == ("c" * 64,)


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("format_version", 2, "version is unsupported"),
        ("application_version", "99.0", "version is incompatible"),
        ("database_dump_sha256", "not-a-hash", "values are invalid"),
        ("receipt_archive_root", "relative", "values are invalid"),
        ("receipt_hashes", "c" * 64, "receipt hashes are invalid"),
    ],
)
def test_manifest_rejects_unsupported_or_malformed_values(
    tmp_path: Path,
    field: str,
    invalid: object,
    message: str,
) -> None:
    """Fail closed on incompatible versions, paths, hashes, or field types."""

    payload = _manifest()
    payload[field] = invalid
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BackupError, match=message):
        AuthoritativeBackupService._load_manifest(path)


def test_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    """Prevent an unversioned manifest extension from changing restore meaning."""

    payload = _manifest()
    payload["unexpected"] = True
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BackupError, match="shape is invalid"):
        AuthoritativeBackupService._load_manifest(path)


def test_operation_lock_refuses_overlapping_local_work(tmp_path: Path) -> None:
    """Use the real kernel lock to reject a concurrent backup or restore."""

    service = AuthoritativeBackupService(_settings(tmp_path))

    with service._operation_lock():
        with pytest.raises(BackupError, match="another backup or restore"):
            with service._operation_lock():
                pytest.fail("a second operation unexpectedly acquired the lock")


def test_failed_subprocess_reports_only_safe_executable_name() -> None:
    """Avoid echoing secret-bearing environment or command arguments on failure."""

    with pytest.raises(
        BackupError, match="authoritative data command failed: false"
    ) as error:
        AuthoritativeBackupService._run(
            ["/bin/false", "super-secret-argument"],
            environment={"SUPER_SECRET": "never-report-this"},
        )

    assert "super-secret" not in str(error.value)
    assert "never-report-this" not in str(error.value)


def test_retention_policy_groups_by_tag_not_staging_path() -> None:
    """Keep every authoritative snapshot in one retention group across runs."""

    policy = dict(zip(RETENTION_ARGUMENTS[::2], RETENTION_ARGUMENTS[1::2], strict=True))

    assert policy == {
        "--group-by": "host,tags",
        "--keep-daily": "7",
        "--keep-weekly": "5",
        "--keep-monthly": "12",
    }
