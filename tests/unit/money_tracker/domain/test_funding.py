"""Tests for funding-source and wallet ledger behavior."""

from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from money_tracker.domain import (
    BalanceMode,
    FundingClass,
    FundingSource,
    Money,
    WalletEntry,
    WalletEntryType,
    WalletError,
    require_available_balance,
    wallet_balance,
)

ROME = ZoneInfo("Europe/Rome")


def _source(*, tracked: bool = True) -> FundingSource:
    """Build one source for wallet unit tests."""

    return FundingSource(
        id=uuid4(),
        profile_id=uuid4(),
        name="Buoni pasto",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.LEDGER if tracked else BalanceMode.UNTRACKED,
    )


def _entry(
    source: FundingSource,
    entry_type: WalletEntryType,
    amount: int,
    *,
    expires_on: date | None = None,
) -> WalletEntry:
    """Build one signed ledger entry for the supplied source."""

    return WalletEntry(
        id=uuid4(),
        profile_id=source.profile_id,
        funding_source_id=source.id,
        entry_type=entry_type,
        amount=Money(amount),
        occurred_at=datetime(2026, 9, 2, 12, tzinfo=ROME),
        expires_on=expires_on,
    )


def test_wallet_balance_sums_immutable_signed_entries() -> None:
    """Derive the booked balance from opening, debit, refund, and adjustment."""

    source = _source()
    entries = (
        _entry(source, WalletEntryType.OPENING, 5000),
        _entry(source, WalletEntryType.DEBIT, -1200),
        _entry(source, WalletEntryType.REFUND, 200),
        _entry(source, WalletEntryType.ADJUSTMENT, -100),
    )

    assert wallet_balance(source, entries) == Money(3900)
    require_available_balance(source, entries, Money(3900))
    with pytest.raises(WalletError, match="insufficient"):
        require_available_balance(source, entries, Money(3901))


def test_untracked_source_does_not_claim_a_balance() -> None:
    """Reject authoritative balance queries for label-only cards or cash."""

    with pytest.raises(WalletError, match="no authoritative balance"):
        wallet_balance(_source(tracked=False), ())


def test_wallet_entry_sign_and_expiry_chronology_are_enforced() -> None:
    """Keep ledger causes signed consistently and reject impossible expiry."""

    source = _source()
    with pytest.raises(WalletError, match="debit.*negative"):
        _entry(source, WalletEntryType.DEBIT, 100)
    with pytest.raises(WalletError, match="cannot precede"):
        _entry(
            source,
            WalletEntryType.CREDIT,
            100,
            expires_on=date(2026, 9, 1),
        )


def test_only_credit_lots_may_have_expiry_dates() -> None:
    """Reject expiry metadata on debit, refund, or adjustment entries."""

    source = _source()
    with pytest.raises(WalletError, match="only opening or credit"):
        _entry(
            source,
            WalletEntryType.ADJUSTMENT,
            100,
            expires_on=date(2026, 10, 1),
        )


def test_wallet_balance_rejects_entries_from_another_source() -> None:
    """Prevent cross-wallet or cross-profile balance contamination."""

    source = _source()
    other = _source()
    with pytest.raises(WalletError, match="another funding source"):
        wallet_balance(source, (_entry(other, WalletEntryType.CREDIT, 100),))


def test_wallet_consumption_must_be_positive() -> None:
    """Keep debit planning input separate from signed persisted ledger rows."""

    source = _source()
    entries = (_entry(source, WalletEntryType.CREDIT, 100),)
    with pytest.raises(WalletError, match="must be positive"):
        require_available_balance(source, entries, Money(-1))
