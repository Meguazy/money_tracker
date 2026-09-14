"""Tests for audited funding-source and wallet administration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from money_tracker.application import (
    FundingSourceAdministrationService,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
)
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    DomainError,
    FundingClass,
    FundingSource,
    Money,
    WalletEntry,
    WalletEntryType,
    WalletError,
    wallet_availability,
)

NOW = datetime(2026, 9, 14, 10, tzinfo=UTC)


@dataclass
class _Clock:
    """Return one deterministic owner observation and audit instant."""

    def now(self) -> datetime:
        """Return an aware instant."""

        return NOW


@dataclass
class _Ids:
    """Supply distinct identifiers for generated audits."""

    def new(self) -> UUID:
        """Return a random UUID."""

        return uuid4()


@dataclass
class _Repository:
    """In-memory implementation of the atomic administration repository."""

    profile_id: UUID
    sources: list[FundingSource] = field(default_factory=list)
    entries: dict[UUID, list[WalletEntry]] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    source_claims: dict[tuple[UUID, str], tuple[str, str, UUID]] = field(
        default_factory=dict
    )
    wallet_claims: dict[tuple[UUID, str], tuple[str, str, UUID]] = field(
        default_factory=dict
    )

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return every source inside the trusted profile."""

        assert profile_id == self.profile_id
        return tuple(self.sources)

    def get_wallet_entries(
        self, profile_id: UUID, funding_source_id: UUID
    ) -> tuple[WalletEntry, ...]:
        """Return one source's immutable wallet history."""

        assert profile_id == self.profile_id
        return tuple(self.entries.get(funding_source_id, ()))

    def get_idempotent_funding_source_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> FundingSource | None:
        """Return the current aggregate for an identical prior source command."""

        prior = self.source_claims.get((profile_id, idempotency_key))
        if prior is None:
            return None
        if prior[:2] != (command_type, request_hash):
            raise IdempotencyConflictError("source command key reused")
        return next(source for source in self.sources if source.id == prior[2])

    def save_funding_source(
        self,
        source: FundingSource,
        *,
        expected_version: int | None,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> FundingSource:
        """Claim, validate, persist, and return one source replacement."""

        claim = (source.profile_id, idempotency_key)
        if claim in self.source_claims:
            prior = self.source_claims[claim]
            if prior[:2] != (command_type, request_hash):
                raise IdempotencyConflictError("source command key reused")
            return next(item for item in self.sources if item.id == prior[2])
        current = next((item for item in self.sources if item.id == source.id), None)
        if expected_version is None:
            assert current is None
            self.sources.append(source)
        else:
            if current is None:
                raise AssertionError("replacement source is missing")
            if current.version != expected_version:
                raise OptimisticConcurrencyError("stale source version")
            self.sources = [
                source if item.id == source.id else item for item in self.sources
            ]
        self.source_claims[claim] = (command_type, request_hash, source.id)
        self.audits.append(audit_event)
        return source

    def set_default_funding_source(
        self,
        source: FundingSource,
        *,
        previous: FundingSource | None,
        expected_version: int,
        expected_previous_version: int | None,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_events: tuple[AuditEvent, ...],
    ) -> None:
        """Move the default flags and audits as one in-memory transaction."""

        current = next(item for item in self.sources if item.id == source.id)
        if current.version != expected_version:
            raise OptimisticConcurrencyError("stale target version")
        actual_previous = next((item for item in self.sources if item.is_default), None)
        if (None if actual_previous is None else actual_previous.version) != (
            expected_previous_version
        ):
            raise OptimisticConcurrencyError("stale default version")
        replacements = {source.id: source}
        if previous is not None:
            replacements[previous.id] = previous
        self.sources = [replacements.get(item.id, item) for item in self.sources]
        self.source_claims[(source.profile_id, idempotency_key)] = (
            command_type,
            request_hash,
            source.id,
        )
        self.audits.extend(audit_events)

    def get_idempotent_wallet_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> WalletEntry | None:
        """Return an identical immutable wallet append."""

        prior = self.wallet_claims.get((profile_id, idempotency_key))
        if prior is None:
            return None
        if prior[:2] != (command_type, request_hash):
            raise IdempotencyConflictError("wallet command key reused")
        return next(
            entry
            for entries in self.entries.values()
            for entry in entries
            if entry.id == prior[2]
        )

    def append_wallet_entry_idempotently(
        self,
        entry: WalletEntry,
        *,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> WalletEntry:
        """Validate availability and store one entry and audit atomically."""

        source = next(
            item for item in self.sources if item.id == entry.funding_source_id
        )
        current = tuple(self.entries.get(source.id, ()))
        wallet_availability(source, (*current, entry), as_of=audit_event.occurred_at)
        self.entries.setdefault(source.id, []).append(entry)
        self.wallet_claims[(entry.profile_id, idempotency_key)] = (
            command_type,
            request_hash,
            entry.id,
        )
        self.audits.append(audit_event)
        return entry


def _source(
    profile_id: UUID,
    *,
    name: str,
    mode: BalanceMode = BalanceMode.UNTRACKED,
    default: bool = False,
) -> FundingSource:
    """Build one active owner source."""

    return FundingSource(
        id=uuid4(),
        profile_id=profile_id,
        name=name,
        funding_class=FundingClass.BENEFIT
        if mode is BalanceMode.LEDGER
        else FundingClass.PERSONAL_FUNDS,
        balance_mode=mode,
        is_default=default,
    )


def _service(repository: _Repository) -> FundingSourceAdministrationService:
    """Build the real service over deterministic external boundaries."""

    return FundingSourceAdministrationService(repository, clock=_Clock(), ids=_Ids())


def _entry(
    source: FundingSource,
    entry_type: WalletEntryType,
    amount: int,
    *,
    occurred_at: datetime = NOW - timedelta(days=1),
    expires_on: date | None = None,
) -> WalletEntry:
    """Build one valid candidate ledger movement."""

    return WalletEntry(
        id=uuid4(),
        profile_id=source.profile_id,
        funding_source_id=source.id,
        entry_type=entry_type,
        amount=Money(amount),
        occurred_at=occurred_at,
        expires_on=expires_on,
    )


def test_management_separates_untracked_labels_from_wallet_availability() -> None:
    """Expose no invented balance for labels and expiry-aware facts for wallets."""

    profile_id = uuid4()
    card = _source(profile_id, name="Carta", default=True)
    wallet = _source(profile_id, name="Benefit", mode=BalanceMode.LEDGER)
    credit = _entry(
        wallet,
        WalletEntryType.CREDIT,
        3000,
        expires_on=date(2026, 9, 30),
    )
    repository = _Repository(
        profile_id,
        [card, wallet],
        entries={wallet.id: [credit]},
    )

    view = _service(repository).management(profile_id=profile_id)

    assert view.observed_at == NOW
    assert view.sources[0].entries == ()
    assert view.sources[0].availability is None
    assert view.sources[1].entries == (credit,)
    assert view.sources[1].availability is not None
    assert view.sources[1].availability.available_balance == Money(3000)


def test_create_and_rename_are_versioned_audited_and_replay_safe() -> None:
    """Commit one source result and audit across identical retries."""

    profile_id = uuid4()
    repository = _Repository(profile_id)
    service = _service(repository)
    source = _source(profile_id, name="Carta")

    created = service.create(
        source,
        idempotency_key="create-source",
        actor="owner:web",
        reason="owner created funding source in browser",
    )
    replayed = service.create(
        replace(source, id=uuid4()),
        idempotency_key="create-source",
        actor="owner:web",
        reason="owner created funding source in browser",
    )
    renamed = service.rename(
        profile_id=profile_id,
        source_id=created.id,
        expected_version=1,
        name="Carta principale",
        idempotency_key="rename-source",
        actor="owner:web",
        reason="owner renamed funding source in browser",
    )

    assert replayed == created
    assert renamed.version == 2
    assert [event.action for event in repository.audits] == ["create", "rename"]


def test_default_move_updates_both_versions_and_emits_two_audits() -> None:
    """Treat the old and new default as one optimistic atomic transition."""

    profile_id = uuid4()
    previous = _source(profile_id, name="Carta", default=True)
    target = _source(profile_id, name="Contanti")
    repository = _Repository(profile_id, [previous, target])

    updated = _service(repository).set_default(
        profile_id=profile_id,
        source_id=target.id,
        expected_version=1,
        expected_previous_default_id=previous.id,
        expected_previous_default_version=1,
        idempotency_key="default-source",
        actor="owner:web",
        reason="owner changed default funding source in browser",
    )

    stored_previous = next(
        item for item in repository.sources if item.id == previous.id
    )
    assert updated.is_default is True
    assert updated.version == 2
    assert stored_previous.is_default is False
    assert stored_previous.version == 2
    assert [event.action for event in repository.audits] == [
        "set_default",
        "unset_default",
    ]


def test_archive_refuses_default_and_nonzero_wallet() -> None:
    """Keep required and financially nonempty sources active."""

    profile_id = uuid4()
    default = _source(profile_id, name="Carta", default=True)
    wallet = _source(profile_id, name="Benefit", mode=BalanceMode.LEDGER)
    repository = _Repository(
        profile_id,
        [default, wallet],
        entries={wallet.id: [_entry(wallet, WalletEntryType.CREDIT, 1000)]},
    )
    service = _service(repository)

    with pytest.raises(DomainError, match="default"):
        service.archive(
            profile_id=profile_id,
            source_id=default.id,
            expected_version=1,
            idempotency_key="archive-default",
            actor="owner:web",
            reason="owner archived funding source in browser",
        )
    with pytest.raises(WalletError, match="zero"):
        service.archive(
            profile_id=profile_id,
            source_id=wallet.id,
            expected_version=1,
            idempotency_key="archive-wallet",
            actor="owner:web",
            reason="owner archived funding source in browser",
        )

    assert repository.audits == []


def test_wallet_append_is_immutable_replay_safe_and_rejects_future_entries() -> None:
    """Append once while refusing chronology and reused-key conflicts."""

    profile_id = uuid4()
    wallet = _source(profile_id, name="Benefit", mode=BalanceMode.LEDGER)
    repository = _Repository(profile_id, [wallet])
    service = _service(repository)
    opening = _entry(wallet, WalletEntryType.OPENING, 2000)

    stored = service.add_wallet_entry(
        opening,
        idempotency_key="wallet-opening",
        actor="owner:web",
        reason="owner added wallet entry in browser",
    )
    replayed = service.add_wallet_entry(
        replace(opening, id=uuid4()),
        idempotency_key="wallet-opening",
        actor="owner:web",
        reason="owner added wallet entry in browser",
    )

    assert replayed == stored
    assert repository.entries[wallet.id] == [stored]
    assert len(repository.audits) == 1
    with pytest.raises(WalletError, match="future"):
        service.add_wallet_entry(
            _entry(
                wallet,
                WalletEntryType.CREDIT,
                100,
                occurred_at=NOW + timedelta(minutes=1),
            ),
            idempotency_key="wallet-future",
            actor="owner:web",
            reason="owner added wallet entry in browser",
        )
