"""Real PostgreSQL tests for the portable finance export."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import connect

from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.export_cli import main as export_main
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.export import (
    FORMAT_NAME,
    FORMAT_VERSION,
    FinanceExportError,
    PostgresFinanceExporter,
)
from money_tracker.persistence.finance import PostgresFinanceRepository


def _fixture(postgres_url: str) -> tuple[Profile, Expense]:
    """Create one real finance aggregate and its immutable audit event."""

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    profile = Profile(uuid4())
    taxonomy = initial_taxonomy()
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta export",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
    )
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1299),
        occurred_at=now,
        description="Contenuto proprietario export",
        merchant="Esercente export",
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(taxonomy.categories[0].id, Money(1299), "owner"),
        ),
        payment_allocations=(PaymentAllocation(source.id, Money(1299)),),
        source_reference="integration:finance-export",
        idempotency_key=f"integration:finance-export:{uuid4()}",
    )
    audit = AuditEvent(
        id=uuid4(),
        profile_id=profile.id,
        aggregate_type="expense",
        aggregate_id=expense.id,
        action="created",
        actor="integration",
        source_reference=expense.source_reference,
        occurred_at=now,
        prior_version=None,
        current_version=expense.version,
        reason="portable export integration",
    )
    repository = PostgresFinanceRepository(postgres_url)
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    repository.create_funding_source(source, occurred_at=now)
    repository.create_expense(
        expense,
        request_hash="e" * 64,
        audit_event=audit,
    )
    return profile, expense


def _records(path: Path) -> list[dict[str, object]]:
    """Read every JSON object from an exported JSON Lines file."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.postgres
def test_export_is_private_complete_versioned_and_cli_reachable(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Export real records through the adapter and production CLI."""

    upgrade_database(postgres_url)
    profile, expense = _fixture(postgres_url)
    destination_root = tmp_path / "private"
    destination_root.mkdir(mode=0o700)
    destination = destination_root / "finance.jsonl"
    exported_at = datetime(2026, 9, 19, 18, tzinfo=UTC)

    result = PostgresFinanceExporter(
        postgres_url,
        clock=lambda: exported_at,
    ).export(
        profile.id,
        destination,
        forbidden_root=Path.cwd(),
    )

    content = destination.read_bytes()
    records = _records(destination)
    manifest = records[0]
    assert destination.stat().st_mode & 0o777 == 0o600
    assert result.sha256 == f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert result.byte_count == len(content)
    assert manifest == {
        "type": "manifest",
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "database_revision": "20260910_0007",
        "exported_at": exported_at.isoformat(),
        "profile_id": str(profile.id),
        "record_counts": result.record_counts,
    }
    assert result.record_counts["profile"] == 1
    assert result.record_counts["category"] == 24
    assert result.record_counts["expense"] == 1
    assert result.record_counts["category_allocation"] == 1
    assert result.record_counts["payment_allocation"] == 1
    assert result.record_counts["audit_event"] == 1
    assert "idempotency_record" not in result.record_counts
    expense_record = next(record for record in records if record["type"] == "expense")
    assert expense_record["data"] == {
        "id": str(expense.id),
        "profile_id": str(profile.id),
        "amount_minor": 1299,
        "currency": "EUR",
        "occurred_at": expense.occurred_at.isoformat(),
        "description": expense.description,
        "merchant": expense.merchant,
        "status": "draft",
        "source_reference": expense.source_reference,
        "idempotency_key": expense.idempotency_key,
        "original_expense_id": None,
        "receipt_id": None,
        "version": 1,
        "created_at": datetime(2026, 9, 19, 12, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 9, 19, 12, tzinfo=UTC).isoformat(),
    }

    cli_destination = destination_root / "finance-cli.jsonl"
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", postgres_url)
    export_main(["--profile-id", str(profile.id), "--output", str(cli_destination)])
    captured = capsys.readouterr()
    cli_result = json.loads(captured.out)
    assert cli_result["status"] == "exported"
    assert cli_result["record_count"] == result.record_count
    assert str(cli_destination) not in captured.err
    assert expense.description not in captured.err
    assert postgres_url not in captured.err
    assert cli_destination.stat().st_mode & 0o777 == 0o600

    missing_destination = destination_root / "missing-profile.jsonl"
    with pytest.raises(FinanceExportError, match="profile_not_found"):
        PostgresFinanceExporter(postgres_url).export(
            uuid4(),
            missing_destination,
            forbidden_root=Path.cwd(),
        )
    assert not missing_destination.exists()

    with connect(postgres_url) as connection:
        connection.execute(
            "UPDATE public.alembic_version SET version_num = %s",
            ("unsupported_export_revision",),
        )
    unsupported_destination = destination_root / "unsupported-schema.jsonl"
    with pytest.raises(FinanceExportError, match="database_schema_unsupported"):
        PostgresFinanceExporter(postgres_url).export(
            profile.id,
            unsupported_destination,
            forbidden_root=Path.cwd(),
        )
    assert not unsupported_destination.exists()
