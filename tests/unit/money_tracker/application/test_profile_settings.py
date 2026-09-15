"""Tests for the safe owner-facing profile settings snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from money_tracker.application import ProfileSettingsService
from money_tracker.domain import (
    BalanceMode,
    DomainError,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    Profile,
)


@dataclass
class InMemoryProfileSettingsRepository:
    """Small real-contract implementation for deterministic service tests."""

    profile: Profile
    sources: tuple[FundingSource, ...]
    requested_profile_ids: list[UUID]

    def get_profile(self, profile_id: UUID) -> Profile:
        """Return the configured profile and record trusted scope."""

        self.requested_profile_ids.append(profile_id)
        return self.profile

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return configured sources and record trusted scope."""

        self.requested_profile_ids.append(profile_id)
        return self.sources


def _source(
    profile_id: UUID,
    *,
    default: bool,
    status: FundingSourceStatus = FundingSourceStatus.ACTIVE,
) -> FundingSource:
    """Build one valid funding source for settings service tests."""

    return FundingSource(
        id=uuid4(),
        profile_id=profile_id,
        name="Carta test",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        status=status,
        is_default=default,
    )


def test_settings_return_fixed_profile_and_only_active_default_choices() -> None:
    """Expose the persisted V1 contract while retaining trusted profile scope."""

    profile_id = uuid4()
    default = _source(profile_id, default=True)
    archived = _source(
        profile_id,
        default=False,
        status=FundingSourceStatus.ARCHIVED,
    )
    repository = InMemoryProfileSettingsRepository(
        profile=Profile(profile_id, default_funding_source_id=default.id),
        sources=(default, archived),
        requested_profile_ids=[],
    )

    settings = ProfileSettingsService(repository).settings(profile_id=profile_id)

    assert settings.profile.locale == "it-IT"
    assert settings.profile.timezone == "Europe/Rome"
    assert settings.profile.currency == "EUR"
    assert settings.default_funding_source == default
    assert settings.available_default_sources == (default,)
    assert repository.requested_profile_ids == [profile_id, profile_id]


def test_settings_allow_initial_setup_without_sources_or_default() -> None:
    """Represent incomplete first-run configuration as an actionable empty state."""

    profile = Profile(uuid4())
    repository = InMemoryProfileSettingsRepository(profile, (), [])

    settings = ProfileSettingsService(repository).settings(profile_id=profile.id)

    assert settings.default_funding_source is None
    assert settings.available_default_sources == ()


@pytest.mark.parametrize("pointer_matches", [True, False])
def test_settings_reject_inconsistent_default_reference(pointer_matches: bool) -> None:
    """Refuse a profile pointer and source flags that do not identify one default."""

    profile_id = uuid4()
    source = _source(profile_id, default=not pointer_matches)
    pointer = source.id if pointer_matches else uuid4()
    repository = InMemoryProfileSettingsRepository(
        Profile(profile_id, default_funding_source_id=pointer),
        (source,),
        [],
    )
    with pytest.raises(
        DomainError,
        match="profile default source state is inconsistent",
    ):
        ProfileSettingsService(repository).settings(profile_id=profile_id)
