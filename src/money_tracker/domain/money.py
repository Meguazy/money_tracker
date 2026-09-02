"""Exact monetary value objects for the Money Tracker domain."""

from __future__ import annotations

from dataclasses import dataclass

from money_tracker.domain.errors import DomainError

V1_CURRENCY = "EUR"


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An exact amount represented in ISO-currency minor units.

    Binary floating-point values never enter this contract. V1 accepts only EUR,
    while retaining the currency field so persistence and APIs remain explicit.

    Attributes:
        minor_units: Signed number of cents.
        currency: Three-letter ISO currency code.
    """

    minor_units: int
    currency: str = V1_CURRENCY

    def __post_init__(self) -> None:
        """Validate the exact representation and the V1 currency boundary."""

        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int):
            raise DomainError("money minor_units must be an integer")
        if self.currency != V1_CURRENCY:
            raise DomainError(f"V1 supports only {V1_CURRENCY}")

    @classmethod
    def zero(cls, currency: str = V1_CURRENCY) -> Money:
        """Create a zero amount in the requested supported currency.

        Args:
            currency: Three-letter ISO currency code.

        Returns:
            An exact zero amount.
        """

        return cls(0, currency)

    def __add__(self, other: Money) -> Money:
        """Add an amount with the same currency.

        Args:
            other: Amount to add.

        Returns:
            Exact sum in minor units.

        Raises:
            DomainError: If currencies differ.
        """

        self._require_same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        """Subtract an amount with the same currency.

        Args:
            other: Amount to subtract.

        Returns:
            Exact difference in minor units.

        Raises:
            DomainError: If currencies differ.
        """

        self._require_same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __neg__(self) -> Money:
        """Return the amount with reversed polarity."""

        return Money(-self.minor_units, self.currency)

    def _require_same_currency(self, other: Money) -> None:
        """Reject arithmetic across currencies.

        Args:
            other: Other operand.

        Raises:
            DomainError: If the currency codes differ.
        """

        if self.currency != other.currency:
            raise DomainError("money arithmetic requires matching currencies")


def sum_money(amounts: tuple[Money, ...], *, currency: str = V1_CURRENCY) -> Money:
    """Sum exact amounts without using Python's integer zero sentinel.

    Args:
        amounts: Amounts to sum.
        currency: Currency used when the sequence is empty.

    Returns:
        Exact sum.

    Raises:
        DomainError: If any amount uses a different currency.
    """

    total = Money.zero(currency)
    for amount in amounts:
        total += amount
    return total
