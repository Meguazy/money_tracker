"""Encrypted authoritative backup and isolated restore orchestration."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from psycopg import connect

from money_tracker.domain.receipts import SHA256_PATTERN
from money_tracker.structured_logging import get_logger

AUTHORITATIVE_TAG = "money-tracker-authoritative"
FINANCE_SCHEMAS = ("finance", "messaging", "workflow")

# Retention groups snapshots by host and tag instead of the Restic default host
# and paths. Each run stages its dump under a fresh private directory, so path
# grouping would place every snapshot in its own group and retain all of them.
RETENTION_ARGUMENTS = (
    "--group-by",
    "host,tags",
    "--keep-daily",
    "7",
    "--keep-weekly",
    "5",
    "--keep-monthly",
    "12",
)


_LOGGER = get_logger("money_tracker.backup")


class BackupError(RuntimeError):
    """Raised when backup or restore safety and integrity checks fail."""


@dataclass(frozen=True, slots=True)
class BackupSettings:
    """Explicit paths and identity constraints for authoritative backups.

    Attributes:
        database_url: Credential-bearing PostgreSQL URL, never passed in argv.
        expected_database: Exact database identity allowed for backup.
        receipt_archive_root: Private authoritative receipt archive.
        config_file: Mode-restricted runtime configuration included encrypted.
        restic_repository: Owner-selected absolute Restic repository path.
        restic_password_file: Mode-restricted Restic recovery-secret file.
        lock_file: Host-local operation lock.
        staging_parent: Protected parent for temporary dumps and manifests.
        restic_binary: Absolute Restic executable.
        pg_dump_binary: Absolute pg_dump executable.
        pg_restore_binary: Absolute pg_restore executable.
    """

    database_url: str
    expected_database: str
    receipt_archive_root: Path
    config_file: Path
    restic_repository: Path
    restic_password_file: Path
    lock_file: Path
    staging_parent: Path
    restic_binary: Path = Path("/usr/bin/restic")
    pg_dump_binary: Path = Path("/usr/bin/pg_dump")
    pg_restore_binary: Path = Path("/usr/bin/pg_restore")

    @classmethod
    def from_env(cls) -> BackupSettings:
        """Load backup settings from explicit environment variables.

        Returns:
            Validated settings without reading or exposing secret values.

        Raises:
            BackupError: If required values or absolute paths are invalid.
        """

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise BackupError(f"{name} is required")
            return value

        return cls(
            database_url=required("MONEY_TRACKER_DATABASE_URL"),
            expected_database=required("MONEY_TRACKER_BACKUP_EXPECTED_DATABASE"),
            receipt_archive_root=_absolute_env_path(
                "MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT"
            ),
            config_file=_absolute_env_path("MONEY_TRACKER_BACKUP_CONFIG_FILE"),
            restic_repository=_absolute_env_path("MONEY_TRACKER_RESTIC_REPOSITORY"),
            restic_password_file=_absolute_env_path(
                "MONEY_TRACKER_RESTIC_PASSWORD_FILE"
            ),
            lock_file=_absolute_env_path("MONEY_TRACKER_BACKUP_LOCK_FILE"),
            staging_parent=_absolute_env_path("MONEY_TRACKER_BACKUP_STAGING_PARENT"),
            restic_binary=_optional_absolute_env_path(
                "MONEY_TRACKER_RESTIC_BINARY", "/usr/bin/restic"
            ),
            pg_dump_binary=_optional_absolute_env_path(
                "MONEY_TRACKER_PG_DUMP_BINARY", "/usr/bin/pg_dump"
            ),
            pg_restore_binary=_optional_absolute_env_path(
                "MONEY_TRACKER_PG_RESTORE_BINARY", "/usr/bin/pg_restore"
            ),
        )


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Integrity facts stored inside one encrypted authoritative snapshot."""

    format_version: int
    application_version: str
    database_name: str
    alembic_revision: str
    database_dump_sha256: str
    config_sha256: str
    receipt_archive_root: str
    config_file: str
    receipt_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Safe result emitted after snapshot verification and retention."""

    snapshot_id: str
    receipt_count: int
    database_name: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Safe result emitted after an isolated restore and integrity checks."""

    snapshot_id: str
    receipt_count: int
    database_name: str


@dataclass(frozen=True, slots=True)
class BackupHealth:
    """Freshness facts about the newest retained authoritative snapshot.

    Attributes:
        snapshot_id: Full Restic identifier of the newest tagged snapshot.
        created_at: Aware creation time reported by the repository.
        age_hours: Hours elapsed since that snapshot was created.
    """

    snapshot_id: str
    created_at: datetime
    age_hours: float


def _absolute_env_path(name: str) -> Path:
    """Read a required absolute path without expanding user-controlled syntax."""

    value = os.getenv(name, "").strip()
    if not value:
        raise BackupError(f"{name} is required")
    path = Path(value)
    if not path.is_absolute():
        raise BackupError(f"{name} must be absolute")
    return path


def _optional_absolute_env_path(name: str, default: str) -> Path:
    """Read an optional absolute executable path."""

    value = os.getenv(name, default).strip()
    path = Path(value)
    if not path.is_absolute():
        raise BackupError(f"{name} must be absolute")
    return path


def _sha256_file(path: Path) -> str:
    """Hash a regular file without following a final symlink."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as error:
        raise BackupError(f"cannot safely read required file: {path.name}") from error
    return digest


def _require_private_file(path: Path, label: str) -> None:
    """Require a regular, owner-only file for secret-bearing configuration."""

    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise BackupError(f"{label} is not readable") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BackupError(f"{label} must be a private regular file")


def _prepare_private_directory(path: Path, label: str) -> None:
    """Create or validate an owner-only directory without accepting a symlink."""

    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True)
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise BackupError(f"{label} is not safely accessible") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise BackupError(f"{label} must be a directory, not a symlink or file")
    os.chmod(path, 0o700)


def _require_empty_restore_target(path: Path) -> None:
    """Accept only an absent or empty real directory as a restore target."""

    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise BackupError("restore receipt target is not safely accessible") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise BackupError("restore receipt target must be a real directory")
    if any(path.iterdir()):
        raise BackupError("restore receipt target must be empty")


def _libpq_environment(database_url: str) -> tuple[dict[str, str], str]:
    """Convert a PostgreSQL URL into libpq environment without argv secrets."""

    parsed = urlparse(database_url)
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or parsed.hostname is None
        or parsed.username is None
        or parsed.path in {"", "/"}
    ):
        raise BackupError("database URL is not a complete PostgreSQL connection")
    database_name = unquote(parsed.path.removeprefix("/"))
    environment = os.environ.copy()
    environment.update(
        {
            "PGHOST": parsed.hostname,
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": unquote(parsed.username),
            "PGDATABASE": database_name,
        }
    )
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)
    return environment, database_name


def _libpq_dsn(database_url: str) -> str:
    """Remove the SQLAlchemy driver marker that libpq cannot parse.

    Args:
        database_url: Configured URL in either accepted application form.

    Returns:
        A connection string psycopg can pass directly to libpq.
    """

    prefix = "postgresql+psycopg://"
    if database_url.startswith(prefix):
        return "postgresql://" + database_url[len(prefix) :]
    return database_url


class AuthoritativeBackupService:
    """Create verified encrypted snapshots and isolated restores."""

    def __init__(self, settings: BackupSettings) -> None:
        """Bind orchestration to explicit local infrastructure settings."""

        self._settings = settings

    def initialize_repository(self) -> None:
        """Initialize an empty configured Restic repository."""

        self._validate_common(require_repository=False)
        self._settings.restic_repository.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(self._settings.restic_repository.iterdir()):
            _LOGGER.error("backup.repository.not_empty")
            raise BackupError("Restic repository initialization requires an empty path")
        self._run_restic("init")
        _LOGGER.info("backup.repository.created")

    def create_backup(self) -> BackupResult:
        """Create, verify, then apply retention to one authoritative snapshot."""

        # A backup that silently stops running is the failure mode that only
        # shows up when a restore is needed, so each stage is recorded as it
        # completes. Repository paths and the database URL stay out: the URL
        # carries a password and the paths are the owner's private layout.
        self._validate_common(require_repository=True)
        with self._operation_lock(), _LOGGER.timed("backup.create") as timing:
            database_name, revision, receipt_hashes = self._preflight_source()
            timing["database_name"] = database_name
            timing["alembic_revision"] = revision
            timing["receipt_count"] = len(receipt_hashes)
            _prepare_private_directory(self._settings.staging_parent, "staging parent")
            with tempfile.TemporaryDirectory(
                prefix="money-tracker-backup-",
                dir=self._settings.staging_parent,
            ) as temporary:
                staging = Path(temporary)
                dump_path = staging / "database.dump"
                manifest_path = staging / "manifest.json"
                self._dump_database(dump_path)
                manifest = BackupManifest(
                    format_version=1,
                    application_version=version("money-tracker"),
                    database_name=database_name,
                    alembic_revision=revision,
                    database_dump_sha256=_sha256_file(dump_path),
                    config_sha256=_sha256_file(self._settings.config_file),
                    receipt_archive_root=str(self._settings.receipt_archive_root),
                    config_file=str(self._settings.config_file),
                    receipt_hashes=receipt_hashes,
                )
                manifest_path.write_text(
                    json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.chmod(manifest_path, 0o600)
                snapshot_id = self._backup_sources(staging)
                timing["snapshot_id"] = snapshot_id
                _LOGGER.debug("backup.snapshot.verifying", snapshot_id=snapshot_id)
                self._run_restic("check")
                self._verify_snapshot(snapshot_id)
                _LOGGER.debug("backup.retention.applying", snapshot_id=snapshot_id)
                self._run_restic(
                    "forget",
                    "--tag",
                    AUTHORITATIVE_TAG,
                    *RETENTION_ARGUMENTS,
                    "--prune",
                )
        return BackupResult(snapshot_id, len(receipt_hashes), database_name)

    def latest_snapshot(self) -> BackupHealth | None:
        """Report the newest authoritative snapshot for local health checks.

        Returns:
            Freshness facts for the newest tagged snapshot, or None when the
            initialized repository holds no authoritative snapshot yet.

        Raises:
            BackupError: If tooling, private inputs, or the repository are
                unavailable, or Restic reports an unusable snapshot listing.
        """

        self._validate_common(require_repository=True)
        payload = json.loads(
            self._run_restic("snapshots", "--json", "--tag", AUTHORITATIVE_TAG)
        )
        if not isinstance(payload, list):
            raise BackupError("Restic snapshot listing is unusable")
        newest: BackupHealth | None = None
        now = datetime.now(UTC)
        for entry in payload:
            snapshot_id = entry.get("id") if isinstance(entry, dict) else None
            created_raw = entry.get("time") if isinstance(entry, dict) else None
            if not isinstance(snapshot_id, str) or not isinstance(created_raw, str):
                raise BackupError("Restic snapshot listing is unusable")
            try:
                created_at = datetime.fromisoformat(created_raw)
            except ValueError as error:
                raise BackupError("Restic snapshot time is unusable") from error
            if created_at.tzinfo is None:
                raise BackupError("Restic snapshot time is not time-zone aware")
            if newest is not None and created_at <= newest.created_at:
                continue
            newest = BackupHealth(
                snapshot_id=snapshot_id,
                created_at=created_at,
                age_hours=(now - created_at).total_seconds() / 3600,
            )
        return newest

    def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        target_database_url: str,
        target_database_name: str,
        target_receipt_root: Path,
    ) -> RestoreResult:
        """Restore one snapshot only into empty isolated database and media targets."""

        if not snapshot_id or any(
            char not in "0123456789abcdef" for char in snapshot_id
        ):
            raise BackupError("snapshot ID must be lowercase hexadecimal")
        if not target_receipt_root.is_absolute():
            raise BackupError("restore receipt target must be absolute")
        self._validate_common(require_repository=True)
        _require_empty_restore_target(target_receipt_root)
        _prepare_private_directory(self._settings.staging_parent, "staging parent")
        _LOGGER.info(
            "backup.restore.started",
            snapshot_id=snapshot_id,
            target_database_name=target_database_name,
        )
        with self._operation_lock():
            self._require_empty_database(target_database_url, target_database_name)
            with tempfile.TemporaryDirectory(
                prefix="money-tracker-restore-",
                dir=self._settings.staging_parent,
            ) as temporary:
                restored = Path(temporary)
                self._run_restic("restore", snapshot_id, "--target", str(restored))
                manifests = tuple(restored.rglob("manifest.json"))
                if len(manifests) != 1:
                    _LOGGER.error(
                        "backup.restore.manifest_ambiguous",
                        snapshot_id=snapshot_id,
                        manifests=len(manifests),
                    )
                    raise BackupError("snapshot must contain exactly one manifest")
                manifest_path = manifests[0]
                manifest = self._load_manifest(manifest_path)
                dump_path = manifest_path.parent / "database.dump"
                if _sha256_file(dump_path) != manifest.database_dump_sha256:
                    # A checksum mismatch means the snapshot cannot be trusted
                    # to restore the ledger, which is as serious as it gets.
                    _LOGGER.critical(
                        "backup.restore.dump_checksum_mismatch",
                        snapshot_id=snapshot_id,
                    )
                    raise BackupError("database dump checksum mismatch")
                restored_config = _restored_absolute(
                    restored, Path(manifest.config_file)
                )
                if _sha256_file(restored_config) != manifest.config_sha256:
                    _LOGGER.critical(
                        "backup.restore.config_checksum_mismatch",
                        snapshot_id=snapshot_id,
                    )
                    raise BackupError("configuration checksum mismatch")
                restored_receipts = _restored_absolute(
                    restored, Path(manifest.receipt_archive_root)
                )
                self._verify_receipt_tree(restored_receipts, manifest.receipt_hashes)
                self._restore_database(
                    dump_path, target_database_url, target_database_name
                )
                _require_empty_restore_target(target_receipt_root)
                _prepare_private_directory(
                    target_receipt_root, "restore receipt target"
                )
                self._copy_receipts(restored_receipts, target_receipt_root)
                self._verify_receipt_tree(target_receipt_root, manifest.receipt_hashes)
        _LOGGER.info(
            "backup.restore.verified",
            snapshot_id=snapshot_id,
            target_database_name=target_database_name,
            receipt_count=len(manifest.receipt_hashes),
        )
        return RestoreResult(
            snapshot_id, len(manifest.receipt_hashes), target_database_name
        )

    def _validate_common(self, *, require_repository: bool) -> None:
        """Validate executables, private inputs, and repository identity."""

        for executable in (
            self._settings.restic_binary,
            self._settings.pg_dump_binary,
            self._settings.pg_restore_binary,
        ):
            if not executable.is_absolute() or not os.access(executable, os.X_OK):
                raise BackupError(
                    f"required executable is unavailable: {executable.name}"
                )
        _require_private_file(self._settings.restic_password_file, "Restic password")
        _require_private_file(self._settings.config_file, "runtime configuration")
        if not self._settings.receipt_archive_root.is_absolute():
            raise BackupError("receipt archive root must be absolute")
        if require_repository and not self._settings.restic_repository.is_dir():
            _LOGGER.error("backup.repository.uninitialized")
            raise BackupError("Restic repository is not initialized")

    @contextmanager
    def _operation_lock(self) -> Generator[None]:
        """Acquire the exact host lock without waiting behind another operation."""

        self._settings.lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._settings.lock_file.open("a+", encoding="utf-8") as handle:
            os.chmod(self._settings.lock_file, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BackupError(
                    "another backup or restore operation is active"
                ) from error
            yield

    def _preflight_source(self) -> tuple[str, str, tuple[str, ...]]:
        """Verify database identity and every registered receipt hash."""

        _, database_name = _libpq_environment(self._settings.database_url)
        if database_name != self._settings.expected_database:
            raise BackupError("configured database name is outside backup scope")
        with connect(_libpq_dsn(self._settings.database_url)) as connection:
            identity = connection.execute("SELECT current_database()").fetchone()
            revision = connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone()
            rows = connection.execute(
                "SELECT content_sha256 FROM finance.receipts ORDER BY content_sha256"
            ).fetchall()
        if identity != (self._settings.expected_database,) or revision is None:
            raise BackupError("database identity or migration state is invalid")
        receipt_hashes = tuple(row[0] for row in rows)
        self._verify_receipt_tree(self._settings.receipt_archive_root, receipt_hashes)
        return database_name, revision[0], receipt_hashes

    def _verify_receipt_tree(self, root: Path, hashes: tuple[str, ...]) -> None:
        """Verify every expected content-addressed receipt without following links."""

        for digest in hashes:
            if SHA256_PATTERN.fullmatch(digest) is None:
                raise BackupError("database contains a noncanonical receipt hash")
            path = root / digest[:2] / digest
            if _sha256_file(path) != digest:
                raise BackupError("receipt archive checksum mismatch")

    def _dump_database(self, target: Path) -> None:
        """Create a custom PostgreSQL dump without placing credentials in argv."""

        environment, database_name = _libpq_environment(self._settings.database_url)
        self._run(
            [
                str(self._settings.pg_dump_binary),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(target),
                "--dbname",
                database_name,
            ],
            environment=environment,
        )
        os.chmod(target, 0o600)

    def _backup_sources(self, staging: Path) -> str:
        """Back up staging, receipts, and configuration in one Restic snapshot."""

        output = self._run_restic(
            "backup",
            "--json",
            "--tag",
            AUTHORITATIVE_TAG,
            str(staging),
            str(self._settings.receipt_archive_root),
            str(self._settings.config_file),
        )
        for line in reversed(output.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshot_id = payload.get("snapshot_id")
            if payload.get("message_type") == "summary" and isinstance(
                snapshot_id, str
            ):
                return snapshot_id
        raise BackupError("Restic did not report a snapshot identifier")

    def _verify_snapshot(self, snapshot_id: str) -> None:
        """Require the exact new snapshot to be visible with the authoritative tag."""

        payload = json.loads(self._run_restic("snapshots", "--json", snapshot_id))
        if (
            not isinstance(payload, list)
            or len(payload) != 1
            or payload[0].get("short_id") != snapshot_id[:8]
            or AUTHORITATIVE_TAG not in payload[0].get("tags", [])
        ):
            raise BackupError("new Restic snapshot verification failed")

    def _require_empty_database(self, database_url: str, expected_name: str) -> None:
        """Refuse restore when the exact isolated target contains owned schemas."""

        _, parsed_name = _libpq_environment(database_url)
        if parsed_name != expected_name:
            raise BackupError("restore database name is outside requested scope")
        with connect(_libpq_dsn(database_url)) as connection:
            identity = connection.execute("SELECT current_database()").fetchone()
            schemas = connection.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = ANY(%s)",
                (list(FINANCE_SCHEMAS),),
            ).fetchall()
            migration = connection.execute(
                "SELECT to_regclass('public.alembic_version')"
            ).fetchone()
        if identity != (expected_name,) or schemas or migration != (None,):
            raise BackupError("restore database must be an empty isolated target")

    def _restore_database(
        self, dump_path: Path, database_url: str, database_name: str
    ) -> None:
        """Import the verified dump into an already validated empty database."""

        environment, _ = _libpq_environment(database_url)
        self._run(
            [
                str(self._settings.pg_restore_binary),
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                database_name,
                str(dump_path),
            ],
            environment=environment,
        )

    @staticmethod
    def _copy_receipts(source: Path, target: Path) -> None:
        """Copy verified receipt files without accepting symlinks."""

        for directory, subdirectories, filenames in os.walk(source, followlinks=False):
            base = Path(directory)
            relative = base.relative_to(source)
            destination = target / relative
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(destination, 0o700)
            for name in tuple(subdirectories) + tuple(filenames):
                if (base / name).is_symlink():
                    raise BackupError("restored receipt tree contains a symlink")
            for filename in filenames:
                shutil.copyfile(base / filename, destination / filename)
                os.chmod(destination / filename, 0o600)

    @staticmethod
    def _load_manifest(path: Path) -> BackupManifest:
        """Load the versioned manifest while rejecting unknown fields."""

        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "format_version",
                "application_version",
                "database_name",
                "alembic_revision",
                "database_dump_sha256",
                "config_sha256",
                "receipt_archive_root",
                "config_file",
                "receipt_hashes",
            }:
                raise BackupError("backup manifest shape is invalid")
            if not isinstance(payload["receipt_hashes"], list) or not all(
                isinstance(item, str) for item in payload["receipt_hashes"]
            ):
                raise BackupError("backup manifest receipt hashes are invalid")
            payload["receipt_hashes"] = tuple(payload["receipt_hashes"])
            manifest = BackupManifest(**payload)
        except (OSError, TypeError, ValueError, KeyError) as error:
            raise BackupError("backup manifest is invalid") from error
        if manifest.format_version != 1:
            raise BackupError("backup manifest version is unsupported")
        if manifest.application_version != version("money-tracker"):
            raise BackupError("backup application version is incompatible")
        if (
            not manifest.database_name
            or not manifest.alembic_revision
            or not isinstance(manifest.database_dump_sha256, str)
            or SHA256_PATTERN.fullmatch(manifest.database_dump_sha256) is None
            or not isinstance(manifest.config_sha256, str)
            or SHA256_PATTERN.fullmatch(manifest.config_sha256) is None
            or not isinstance(manifest.receipt_archive_root, str)
            or not Path(manifest.receipt_archive_root).is_absolute()
            or not isinstance(manifest.config_file, str)
            or not Path(manifest.config_file).is_absolute()
            or any(
                SHA256_PATTERN.fullmatch(item) is None
                for item in manifest.receipt_hashes
            )
        ):
            raise BackupError("backup manifest values are invalid")
        return manifest

    def _restic_environment(self) -> dict[str, str]:
        """Build Restic environment with secret path but never secret contents."""

        environment = os.environ.copy()
        environment.update(
            {
                "RESTIC_REPOSITORY": str(self._settings.restic_repository),
                "RESTIC_PASSWORD_FILE": str(self._settings.restic_password_file),
            }
        )
        return environment

    def _run_restic(self, *arguments: str) -> str:
        """Run one Restic command and return captured stdout."""

        return self._run(
            [str(self._settings.restic_binary), *arguments],
            environment=self._restic_environment(),
        )

    @staticmethod
    def _run(command: list[str], *, environment: dict[str, str]) -> str:
        """Run a bounded local process without a shell or inherited stdin."""

        try:
            completed = subprocess.run(
                command,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise BackupError(
                f"authoritative data command failed: {Path(command[0]).name}"
            ) from error
        return completed.stdout


def _restored_absolute(restore_root: Path, original: Path) -> Path:
    """Map a Restic-restored absolute source beneath its isolated target."""

    if not original.is_absolute():
        raise BackupError("manifest source path must be absolute")
    relative = original.relative_to("/")
    if ".." in relative.parts:
        raise BackupError("manifest source path escapes restore staging")
    candidate = restore_root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise BackupError("restored source path contains a symlink")
    return candidate
