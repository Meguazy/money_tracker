"""Primitives for addressing and checksumming local authoritative data.

The backup and recovery components both handle the owner's authoritative data
outside the application runtime: one writes and restores it, the other verifies
what a restore produced. They therefore need the same two low-level guarantees
before either can trust a target, and neither may depend on the other to get
them.

This module deliberately imports nothing beyond the standard library so that
operator commands stay cheap to start and free of runtime, migration, or
checkpoint dependencies.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_SQLALCHEMY_DRIVER_PREFIX = "postgresql+psycopg://"
_LIBPQ_PREFIX = "postgresql://"


class LocalIntegrityError(RuntimeError):
    """Raised when a required local file cannot be read safely."""


def sha256_file(path: Path) -> str:
    """Hash a regular file without following a final symlink.

    The no-follow open is the security boundary: a restored or archived path is
    attacker-influenced in the threat model, and following a final symlink would
    hash a file outside the private tree while reporting the expected name.

    Args:
        path: File to hash, opened without following a final symlink.

    Returns:
        The lowercase hexadecimal SHA-256 digest of the file's bytes.

    Raises:
        LocalIntegrityError: If the file cannot be opened or read safely.
    """

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as error:
        raise LocalIntegrityError(
            f"cannot safely read required file: {path.name}"
        ) from error


def libpq_dsn(database_url: str) -> str:
    """Remove the SQLAlchemy driver marker that libpq cannot parse.

    Args:
        database_url: Configured URL in either accepted application form.

    Returns:
        A connection string psycopg can pass directly to libpq.
    """

    if database_url.startswith(_SQLALCHEMY_DRIVER_PREFIX):
        return _LIBPQ_PREFIX + database_url[len(_SQLALCHEMY_DRIVER_PREFIX) :]
    return database_url
