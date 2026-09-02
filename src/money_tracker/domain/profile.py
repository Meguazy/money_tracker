"""Owner profile contract and fixed V1 locale boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from money_tracker.domain.errors import DomainError
from money_tracker.domain.money import V1_CURRENCY

V1_LOCALE = "it-IT"
V1_TIMEZONE = "Europe/Rome"


@dataclass(frozen=True, slots=True)
class Profile:
    """Stable owner scope and finance defaults.

    Attributes:
        id: Stable profile identifier.
        locale: User-facing locale.
        timezone: IANA timezone governing reporting periods.
        currency: Operational ISO currency.
        default_funding_source_id: Source used when text ingestion omits payment.
    """

    id: UUID
    locale: str = V1_LOCALE
    timezone: str = V1_TIMEZONE
    currency: str = V1_CURRENCY
    default_funding_source_id: UUID | None = None

    def __post_init__(self) -> None:
        """Enforce the accepted single-locale V1 operating contract."""

        if self.locale != V1_LOCALE:
            raise DomainError(f"V1 profile locale must be {V1_LOCALE}")
        if self.timezone != V1_TIMEZONE:
            raise DomainError(f"V1 profile timezone must be {V1_TIMEZONE}")
        if self.currency != V1_CURRENCY:
            raise DomainError(f"V1 profile currency must be {V1_CURRENCY}")
