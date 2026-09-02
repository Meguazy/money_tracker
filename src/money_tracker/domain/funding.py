"""Funding-source and lightweight wallet ledger contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from money_tracker.domain.errors import DomainError, WalletError
from money_tracker.domain.money import Money, sum_money


class FundingClass(StrEnum):
    """Economic origin of a payment resource."""

    PERSONAL_FUNDS = "personal_funds"
    BENEFIT = "benefit"
    OTHER = "other"


class BalanceMode(StrEnum):
    """Whether Money Tracker claims a ledger-derived source balance."""

    UNTRACKED = "untracked"
    LEDGER = "ledger"


class FundingSourceStatus(StrEnum):
    """Whether a source may receive new payment allocations."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class WalletEntryType(StrEnum):
    """Immutable causes contributing to a tracked wallet balance."""

    OPENING = "opening"
    CREDIT = "credit"
    DEBIT = "debit"
    REFUND = "refund"
    EXPIRY = "expiry"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class FundingSource:
    """Payment resource, optionally backed by a lightweight ledger.

    Attributes:
        id: Stable identifier.
        profile_id: Owner scope.
        name: Owner-facing source name.
        funding_class: Personal funds, benefit, or another resource class.
        balance_mode: Whether ledger entries define a claimed balance.
        status: Whether new allocations are permitted.
        is_default: Whether omitted text payments resolve to this source.
    """

    id: UUID
    profile_id: UUID
    name: str
    funding_class: FundingClass
    balance_mode: BalanceMode
    status: FundingSourceStatus = FundingSourceStatus.ACTIVE
    is_default: bool = False

    def __post_init__(self) -> None:
        """Reject an unnamed funding source."""

        if not self.name.strip():
            raise DomainError("funding source name cannot be empty")


@dataclass(frozen=True, slots=True)
class WalletEntry:
    """Immutable signed contribution to a tracked funding-source balance.

    Opening, credit, and refund entries are positive; debit and expiry entries
    are negative. Adjustments may have either polarity but never zero.

    Attributes:
        id: Stable entry identifier.
        profile_id: Owner scope.
        funding_source_id: Owning ledger-mode funding source.
        entry_type: Business cause of the balance movement.
        amount: Signed exact amount.
        occurred_at: Time the entry took effect.
        expires_on: Optional expiry date for a credit lot.
        expense_id: Optional originating expense or refund.
        note: Optional operator explanation.
    """

    id: UUID
    profile_id: UUID
    funding_source_id: UUID
    entry_type: WalletEntryType
    amount: Money
    occurred_at: datetime
    expires_on: date | None = None
    expense_id: UUID | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        """Enforce signed-entry and chronology contracts."""

        if self.amount.minor_units == 0:
            raise WalletError("wallet entry amount cannot be zero")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise WalletError("wallet entry occurrence time must include a timezone")
        positive_types = {
            WalletEntryType.OPENING,
            WalletEntryType.CREDIT,
            WalletEntryType.REFUND,
        }
        negative_types = {WalletEntryType.DEBIT, WalletEntryType.EXPIRY}
        if self.entry_type in positive_types and self.amount.minor_units < 0:
            raise WalletError(f"{self.entry_type.value} wallet entry must be positive")
        if self.entry_type in negative_types and self.amount.minor_units > 0:
            raise WalletError(f"{self.entry_type.value} wallet entry must be negative")
        if self.expires_on is not None:
            if self.entry_type not in {WalletEntryType.OPENING, WalletEntryType.CREDIT}:
                raise WalletError("only opening or credit entries may expire")
            if self.expires_on < self.occurred_at.date():
                raise WalletError("wallet credit expiry cannot precede its credit date")


def wallet_balance(
    source: FundingSource,
    entries: tuple[WalletEntry, ...],
) -> Money:
    """Compute a ledger source balance from immutable signed entries.

    This is the booked balance. Expiry-aware available balance requires explicit
    expiry entries; the function does not silently infer lot consumption.

    Args:
        source: Funding source whose balance is requested.
        entries: Complete ledger entries to include.

    Returns:
        Exact booked balance.

    Raises:
        WalletError: If the source is untracked or an entry belongs elsewhere.
    """

    if source.balance_mode is not BalanceMode.LEDGER:
        raise WalletError("untracked funding source has no authoritative balance")
    for entry in entries:
        if (
            entry.funding_source_id != source.id
            or entry.profile_id != source.profile_id
        ):
            raise WalletError("wallet entry belongs to another funding source")
    return sum_money(tuple(entry.amount for entry in entries))


def require_available_balance(
    source: FundingSource,
    existing_entries: tuple[WalletEntry, ...],
    debit: Money,
) -> None:
    """Reject a planned positive consumption exceeding a tracked wallet balance.

    Args:
        source: Ledger-mode source being consumed.
        existing_entries: Entries already booked for the source.
        debit: Positive amount the expense intends to consume.

    Raises:
        WalletError: If the request is invalid or exceeds the booked balance.
    """

    if debit.minor_units <= 0:
        raise WalletError("planned wallet consumption must be positive")
    if wallet_balance(source, existing_entries).minor_units < debit.minor_units:
        raise WalletError("tracked wallet has insufficient balance")
