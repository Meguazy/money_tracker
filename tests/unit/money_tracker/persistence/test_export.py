"""Unit tests for the portable finance export boundary."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from money_tracker.export_cli import _parser
from money_tracker.persistence.export import (
    FinanceExportError,
    PostgresFinanceExporter,
    _json_value,
    _require_private_destination,
)


def _private_directory(path: Path) -> Path:
    """Create an owner-only directory used by an export test."""

    path.mkdir()
    path.chmod(0o700)
    return path


def test_destination_must_be_new_private_absolute_and_outside_checkout(
    tmp_path: Path,
) -> None:
    """Refuse paths that could be overwritten, exposed, or committed."""

    checkout = _private_directory(tmp_path / "checkout")
    private = _private_directory(tmp_path / "private")
    destination = private / "finance.jsonl"

    _require_private_destination(destination, forbidden_root=checkout)

    existing = private / "existing.jsonl"
    existing.write_text("do not replace", encoding="utf-8")
    with pytest.raises(FinanceExportError, match="export_path_exists"):
        _require_private_destination(existing, forbidden_root=checkout)
    with pytest.raises(FinanceExportError, match="export_path_not_absolute"):
        _require_private_destination(Path("finance.jsonl"), forbidden_root=checkout)
    with pytest.raises(FinanceExportError, match="export_inside_repository"):
        _require_private_destination(
            checkout / "finance.jsonl",
            forbidden_root=checkout,
        )

    shared = tmp_path / "shared"
    shared.mkdir(mode=0o750)
    with pytest.raises(FinanceExportError, match="export_parent_not_private"):
        _require_private_destination(
            shared / "finance.jsonl",
            forbidden_root=checkout,
        )


def test_json_values_preserve_portable_domain_representations() -> None:
    """Keep identifiers, dates, instants, arrays, and minor units unambiguous."""

    identifier = uuid4()
    instant = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)

    assert _json_value(
        {
            "id": identifier,
            "instant": instant,
            "day": date(2026, 9, 19),
            "amount_minor": 1299,
            "references": [identifier, None],
        }
    ) == {
        "id": str(identifier),
        "instant": instant.isoformat(),
        "day": "2026-09-19",
        "amount_minor": 1299,
        "references": [str(identifier), None],
    }
    with pytest.raises(FinanceExportError, match="database_timestamp_naive"):
        _json_value(datetime(2026, 9, 19, 12, 30))
    with pytest.raises(FinanceExportError, match="database_value_unsupported"):
        _json_value(1.25)


def test_export_refuses_a_naive_manifest_clock_before_connecting(
    tmp_path: Path,
) -> None:
    """Never write an ambiguous export timestamp or touch the database."""

    private = _private_directory(tmp_path / "private")
    exporter = PostgresFinanceExporter(
        "postgresql://unused",
        clock=lambda: datetime(2026, 9, 19, 12, 30),
    )
    destination = private / "finance.jsonl"

    with pytest.raises(FinanceExportError, match="export_clock_naive"):
        exporter.export(uuid4(), destination, forbidden_root=tmp_path / "checkout")

    assert not destination.exists()


def test_exporter_accepts_postgresql_psycopg_url_scheme() -> None:
    """Allow the SQLAlchemy-compatible PostgreSQL URL scheme."""

    PostgresFinanceExporter("postgresql+psycopg://unused")


def test_cli_requires_profile_and_output() -> None:
    """Keep profile scope and the private destination explicit."""

    profile_id = uuid4()
    parsed = _parser().parse_args(
        ["--profile-id", str(profile_id), "--output", "/private/finance.jsonl"]
    )

    assert parsed.profile_id == profile_id
    assert parsed.output == Path("/private/finance.jsonl")
    with pytest.raises(SystemExit):
        _parser().parse_args(["--output", "/private/finance.jsonl"])
    with pytest.raises(SystemExit):
        _parser().parse_args(["--profile-id", "not-a-uuid", "--output", "/tmp/x"])
