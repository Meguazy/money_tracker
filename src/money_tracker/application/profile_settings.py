"""Read the safe owner-facing subset of the V1 profile configuration."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from money_tracker.application.ports import ProfileSettingsRepository
from money_tracker.domain import (
    DomainError,
    FundingSource,
    FundingSourceStatus,
    Profile,
)


@dataclass(frozen=True, slots=True)
class ProfileSettings:
    """Safe profile preferences and active choices for the local owner.

    Attributes:
        profile: Trusted persisted profile. Its stable identifier never crosses
            the browser boundary.
        default_funding_source: Active source selected for omitted payments, or
            None while initial setup is incomplete.
        available_default_sources: Active sources eligible to become default.
    """

    profile: Profile
    default_funding_source: FundingSource | None
    available_default_sources: tuple[FundingSource, ...]


class ProfileSettingsService:
    """Compose profile preferences without exposing operational configuration."""

    def __init__(self, repository: ProfileSettingsRepository) -> None:
        """Bind the read model to the authoritative profile repository.

        Args:
            repository: Source of profile and funding-source state.
        """

        self._repository = repository

    def settings(self, *, profile_id: UUID) -> ProfileSettings:
        """Return the safe settings snapshot for one trusted profile.

        Locale, timezone, and currency are persisted profile facts but fixed by
        the V1 domain. The only mutable preference in this snapshot is the
        default funding source, whose writes remain owned by the existing
        funding-source administration service.

        Args:
            profile_id: Trusted owner scope supplied by runtime composition.

        Returns:
            Validated settings and eligible default-source choices.

        Raises:
            DomainError: If the persisted default pointer and source flags do
                not describe one consistent active source.
        """

        profile = self._repository.get_profile(profile_id)
        active_sources = tuple(
            source
            for source in self._repository.get_funding_sources(profile_id)
            if source.status is FundingSourceStatus.ACTIVE
        )
        flagged = tuple(source for source in active_sources if source.is_default)
        if profile.default_funding_source_id is None:
            if flagged:
                raise DomainError("profile default source state is inconsistent")
            default = None
        else:
            matching = tuple(
                source
                for source in flagged
                if source.id == profile.default_funding_source_id
            )
            if len(flagged) != 1 or len(matching) != 1:
                raise DomainError("profile default source state is inconsistent")
            default = matching[0]
        return ProfileSettings(
            profile=profile,
            default_funding_source=default,
            available_default_sources=active_sources,
        )
