"""Narrow replaceable ports required by finance application services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from money_tracker.domain import (
    AuditEvent,
    CategoryProposal,
    Expense,
    FundingSource,
    MonthlyBudget,
    Profile,
    ReceiptMetadata,
    Taxonomy,
    WalletEntry,
)


class Clock(Protocol):
    """Supply aware mutation timestamps under caller control in tests."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""


class IdGenerator(Protocol):
    """Supply stable identifiers without coupling services to UUID generation."""

    def new(self) -> UUID:
        """Return a new stable identifier."""


class FinanceRepository(Protocol):
    """Persist complete aggregates and audit data in one transaction boundary."""

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Load one expense inside the trusted profile scope."""

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Load the complete category view for deterministic validation."""

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Load the profile's funding sources."""

    def create_expense(
        self,
        expense: Expense,
        *,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> Expense:
        """Create once or return the result of an identical prior command."""

    def replace_expense(
        self,
        expense: Expense,
        *,
        expected_version: int,
        audit_event: AuditEvent,
    ) -> None:
        """Replace a complete aggregate only at the expected current version."""

    def bootstrap_profile(
        self,
        profile: Profile,
        *,
        taxonomy: Taxonomy,
        occurred_at: datetime,
    ) -> None:
        """Create the sole profile and accepted taxonomy idempotently."""


class FinanceAdministrationRepository(Protocol):
    """Persist wallet and budget mutations behind one atomic audit boundary."""

    def append_wallet_entry(
        self,
        entry: WalletEntry,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Append one validated wallet entry and audit atomically."""

    def get_budget(self, profile_id: UUID, category_id: UUID) -> MonthlyBudget | None:
        """Load the budget attached to one category when configured."""

    def save_budget(
        self,
        budget: MonthlyBudget,
        *,
        expected_version: int | None,
        audit_event: AuditEvent,
    ) -> None:
        """Create or replace a complete budget under optimistic concurrency."""


class CategoryProposalRepository(Protocol):
    """Persist category candidates without granting taxonomy mutation authority."""

    def create_category_proposal(
        self,
        proposal: CategoryProposal,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Persist one pending proposal and matching audit atomically."""


class ReceiptMetadataRepository(Protocol):
    """Persist content-addressed receipt metadata and review transitions."""

    def get_receipt_metadata(
        self,
        profile_id: UUID,
        receipt_id: UUID,
    ) -> ReceiptMetadata:
        """Load one receipt inside the trusted profile scope."""

    def create_receipt_metadata(
        self,
        metadata: ReceiptMetadata,
        *,
        audit_event: AuditEvent,
    ) -> ReceiptMetadata:
        """Create metadata or return the existing record for the same content."""

    def replace_receipt_metadata(
        self,
        metadata: ReceiptMetadata,
        *,
        expected_version: int,
        audit_event: AuditEvent,
    ) -> None:
        """Replace review state only at the expected current version."""
