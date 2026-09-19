"""Unit tests for the primitives shared by backup and recovery."""

from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from money_tracker.local_integrity import (
    LocalIntegrityError,
    libpq_dsn,
    sha256_file,
)

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:secret@127.0.0.1:5433/money_tracker",
        "postgresql+psycopg://user:secret@127.0.0.1:5433/money_tracker",
    ],
)
def test_accepted_application_urls_produce_a_parsable_libpq_dsn(
    database_url: str,
) -> None:
    """Connect with either configured scheme instead of failing inside libpq."""

    parameters = conninfo_to_dict(libpq_dsn(database_url))

    assert parameters["dbname"] == "money_tracker"
    assert parameters["host"] == "127.0.0.1"
    assert parameters["port"] == "5433"


def test_sha256_file_hashes_a_regular_file(tmp_path: Path) -> None:
    """Produce the canonical digest the manifest and verifier compare against."""

    target = tmp_path / "payload"
    target.write_bytes(b"")

    assert sha256_file(target) == _EMPTY_SHA256


def test_sha256_file_refuses_a_symlinked_target(tmp_path: Path) -> None:
    """Refuse a final symlink instead of hashing a file outside the private tree."""

    outside = tmp_path / "outside"
    outside.write_bytes(b"owner bytes")
    link = tmp_path / "link"
    link.symlink_to(outside)

    with pytest.raises(LocalIntegrityError) as failure:
        sha256_file(link)

    assert "link" in str(failure.value)
    assert "owner bytes" not in str(failure.value)


def test_sha256_file_reports_a_missing_file_without_its_path(tmp_path: Path) -> None:
    """Name only the file's own name so a private directory stays out of the error."""

    missing = tmp_path / "private-directory" / "absent"

    with pytest.raises(LocalIntegrityError) as failure:
        sha256_file(missing)

    assert "private-directory" not in str(failure.value)
