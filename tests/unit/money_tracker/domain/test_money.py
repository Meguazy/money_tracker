"""Tests for exact minor-unit monetary arithmetic."""

import pytest

from money_tracker.domain import DomainError, Money, sum_money


def test_money_uses_exact_integer_minor_units() -> None:
    """Add and subtract cents without accepting binary floating point."""

    assert Money(599) + Money(1201) == Money(1800)
    assert Money(1800) - Money(599) == Money(1201)
    assert -Money(599) == Money(-599)
    with pytest.raises(DomainError, match="integer"):
        Money(5.99)  # type: ignore[arg-type]
    with pytest.raises(DomainError, match="integer"):
        Money(True)


def test_v1_rejects_unsupported_currency() -> None:
    """Retain the currency field while enforcing the accepted EUR boundary."""

    with pytest.raises(DomainError, match="only EUR"):
        Money(100, "USD")


def test_sum_money_has_an_exact_empty_identity() -> None:
    """Sum empty and signed movements with a Money zero, not an integer sentinel."""

    assert sum_money(()) == Money.zero()
    assert sum_money((Money(500), Money(-125), Money(25))) == Money(400)
