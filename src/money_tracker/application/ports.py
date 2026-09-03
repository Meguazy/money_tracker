"""Narrow replaceable ports required by finance application services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from money_tracker.domain import (
    AuditEvent,
    Category,
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


class CategoryReviewRepository(Protocol):
    """Settle owner category decisions, the only path that creates a node.

    Kept separate from `CategoryProposalRepository` because the authority
    differs: submitting a candidate is a workflow act that changes nothing the
    owner has not seen, while settling one can extend the taxonomy and complete
    a movement. AD-008 reserves that authority for an explicit owner decision,
    so it lives behind its own narrow port.
    """

    def get_category_proposal(
        self,
        profile_id: UUID,
        proposal_id: UUID,
    ) -> CategoryProposal:
        """Load one proposal inside its trusted profile scope."""

    def get_pending_category_proposals(
        self,
        profile_id: UUID,
    ) -> tuple[CategoryProposal, ...]:
        """Load every open review in one profile scope, oldest first."""

    def settle_category_review(
        self,
        proposal: CategoryProposal,
        *,
        expense: Expense,
        expected_version: int,
        category: Category | None,
        proposal_audit: AuditEvent,
        expense_audit: AuditEvent,
    ) -> None:
        """Settle one decision, its optional new node, and the movement."""

    def repropose_category(
        self,
        rejected: CategoryProposal,
        successor: CategoryProposal,
        *,
        rejected_audit: AuditEvent,
        successor_audit: AuditEvent,
    ) -> None:
        """Refuse one candidate and open its replacement atomically."""


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
