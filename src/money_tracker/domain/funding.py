"""Funding-source and lightweight wallet ledger contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from money_tracker.domain.errors import DomainError, WalletError
from money_tracker.domain.money import Money, sum_money

_OWNER_TIMEZONE = ZoneInfo("Europe/Rome")


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
        version: Optimistic concurrency version for owner-managed state.
    """

    id: UUID
    profile_id: UUID
    name: str
    funding_class: FundingClass
    balance_mode: BalanceMode
    status: FundingSourceStatus = FundingSourceStatus.ACTIVE
    is_default: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        """Reject an unnamed funding source."""

        if not self.name.strip():
            raise DomainError("funding source name cannot be empty")
        if self.version < 1:
            raise DomainError("funding source version must be positive")


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
            if self.expires_on < self.occurred_at.astimezone(_OWNER_TIMEZONE).date():
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


@dataclass(frozen=True, slots=True)
class ExpiringWalletBalance:
    """Remaining wallet credit attached to one expiry date.

    Attributes:
        expires_on: Last calendar date on which the remaining credit is usable.
        amount: Exact still-available amount carried by credit lots on that date.
    """

    expires_on: date
    amount: Money


@dataclass(frozen=True, slots=True)
class WalletAvailability:
    """Deterministic booked and spendable state for one tracked wallet.

    ``booked_balance`` is the immutable ledger sum. ``available_balance`` also
    removes expired credit and allocates negative entries first to the credit
    lot that expires soonest. This first-expiry-first policy prevents a later
    debit from making an already expired lot look usable.

    Attributes:
        booked_balance: Sum of every entry effective by the observation time.
        available_balance: Non-expired balance after deterministic consumption.
        expiring: Remaining dated lots, ordered by expiry date.
    """

    booked_balance: Money
    available_balance: Money
    expiring: tuple[ExpiringWalletBalance, ...]


@dataclass(slots=True)
class _CreditLot:
    """Mutable internal remainder used while projecting wallet availability."""

    expires_on: date | None
    minor_units: int


def wallet_availability(
    source: FundingSource,
    entries: tuple[WalletEntry, ...],
    *,
    as_of: datetime,
) -> WalletAvailability:
    """Project an expiry-aware tracked-wallet balance with first-expiry-first use.

    Opening and credit entries create individual lots. Debits consume only
    non-expired value, first-expiry-first. Expiry entries and negative
    adjustments reduce booked lots in the same order, including expired value,
    so an immutable write-down cannot make expired credit spendable. Refunds
    and positive adjustments create undated value.
    Credits remain usable through ``expires_on`` and expire the next day.

    Args:
        source: Ledger-mode source whose availability is requested.
        entries: Complete immutable entry history for the source.
        as_of: Aware observation time; future entries are excluded.

    Returns:
        Booked, available, and still-expiring exact balances.

    Raises:
        WalletError: If the source is untracked, the instant is naive, an entry
            belongs elsewhere, a debit exceeds available value, or a write-down
            exceeds booked value.
    """

    if source.balance_mode is not BalanceMode.LEDGER:
        raise WalletError("untracked funding source has no authoritative balance")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise WalletError("wallet availability instant must include a timezone")
    for entry in entries:
        if (
            entry.funding_source_id != source.id
            or entry.profile_id != source.profile_id
        ):
            raise WalletError("wallet entry belongs to another funding source")
    effective = tuple(
        sorted(
            (entry for entry in entries if entry.occurred_at <= as_of),
            key=lambda item: (item.occurred_at, item.id),
        )
    )
    lots: list[_CreditLot] = []
    for entry in effective:
        occurred_on = entry.occurred_at.astimezone(_OWNER_TIMEZONE).date()
        amount = entry.amount.minor_units
        if amount > 0:
            expiry = (
                entry.expires_on
                if entry.entry_type in {WalletEntryType.OPENING, WalletEntryType.CREDIT}
                else None
            )
            lots.append(_CreditLot(expiry, amount))
            continue
        candidates = (
            [
                lot
                for lot in lots
                if lot.expires_on is None or lot.expires_on >= occurred_on
            ]
            if entry.entry_type is WalletEntryType.DEBIT
            else lots
        )
        _consume_lots(candidates, -amount)
        lots = [lot for lot in lots if lot.minor_units > 0]

    as_of_date = as_of.astimezone(_OWNER_TIMEZONE).date()
    available_lots = [
        lot for lot in lots if lot.expires_on is None or lot.expires_on >= as_of_date
    ]
    expiring_totals: dict[date, int] = {}
    for lot in available_lots:
        if lot.expires_on is not None:
            expiring_totals[lot.expires_on] = (
                expiring_totals.get(lot.expires_on, 0) + lot.minor_units
            )
    return WalletAvailability(
        booked_balance=sum_money(
            tuple(entry.amount for entry in effective), currency="EUR"
        ),
        available_balance=Money(sum(lot.minor_units for lot in available_lots)),
        expiring=tuple(
            ExpiringWalletBalance(expires_on=expiry, amount=Money(amount))
            for expiry, amount in sorted(expiring_totals.items())
            if amount > 0
        ),
    )


def _consume_lots(lots: list[_CreditLot], amount_minor: int) -> None:
    """Consume exact value from earliest-expiring lots in place.

    Args:
        lots: Available positive lots.
        amount_minor: Positive number of cents to consume.

    Raises:
        WalletError: If the available lots cannot cover the request.
    """

    remaining = amount_minor
    for lot in sorted(
        lots,
        key=lambda item: (item.expires_on is None, item.expires_on or date.max),
    ):
        consumed = min(lot.minor_units, remaining)
        lot.minor_units -= consumed
        remaining -= consumed
        if remaining == 0:
            break
    lots[:] = [lot for lot in lots if lot.minor_units > 0]
    if remaining:
        raise WalletError("tracked wallet has insufficient available balance")
