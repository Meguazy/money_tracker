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
    YearMonth,
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

    def get_latest_correctable_movement(self, profile_id: UUID) -> UUID | None:
        """Return the movement a correction or undo would target by default.

        A channel correction names no identifier, so the runtime resolves the
        movement the owner most plausibly means: the most recently recorded one
        that has not already reached a terminal state. Returning None is an
        answerable fact rather than an error, because it is what turns an
        uncorrelated correction into a question instead of a guess.
        """

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

    def get_idempotent_expense_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        expense_id: UUID,
    ) -> Expense | None:
        """Return an identical prior mutation result, or None when unclaimed."""

    def replace_expense_idempotently(
        self,
        expense: Expense,
        *,
        expected_version: int,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> Expense:
        """Claim a command and replace once in the same transaction."""

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

    Kept separate from CategoryProposalRepository because the authority
    differs: submitting a candidate is a workflow act that changes nothing the
    owner has not seen, while settling one can extend the taxonomy and complete
    a movement. AD-008 reserves that authority for an explicit owner decision.
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

    def get_idempotent_category_review_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> UUID | None:
        """Return the result identifier of an identical prior decision."""

    def settle_category_review(
        self,
        proposal: CategoryProposal,
        *,
        expense: Expense,
        expected_version: int,
        category: Category | None,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        proposal_audit: AuditEvent,
        expense_audit: AuditEvent,
    ) -> Expense:
        """Settle or return the movement from an identical prior decision."""

    def repropose_category(
        self,
        rejected: CategoryProposal,
        successor: CategoryProposal,
        *,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        rejected_audit: AuditEvent,
        successor_audit: AuditEvent,
    ) -> CategoryProposal:
        """Open or return the replacement from an identical prior decision."""


class ReportingRepository(Protocol):
    """Read-only reads behind the query and advice routes.

    Deliberately narrow and free of every mutation the finance ports expose, so
    a reporting caller has no method by which to write. AD-006 gives the
    bounded agent routes read-only domain tools, and this port is where that is
    enforced by shape rather than by discipline.
    """

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the profile's complete validated taxonomy."""

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the profile's configured funding sources."""

    def get_expenses(
        self,
        profile_id: UUID,
        *,
        period: YearMonth,
    ) -> tuple[Expense, ...]:
        """Return every movement state occurring in one calendar month."""

    def get_posted_expenses(
        self,
        profile_id: UUID,
        *,
        period: YearMonth,
    ) -> tuple[Expense, ...]:
        """Return every posted movement occurring in one calendar month."""

    def get_budget(self, profile_id: UUID, category_id: UUID) -> MonthlyBudget | None:
        """Return one category budget, or None when none is configured."""

    def get_budgets(self, profile_id: UUID) -> tuple[MonthlyBudget, ...]:
        """Return every configured category budget."""


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
