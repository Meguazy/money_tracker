"""Audited owner administration of funding sources and tracked wallets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from money_tracker.application.errors import (
    AggregateNotFoundError,
    OptimisticConcurrencyError,
)
from money_tracker.application.ports import (
    Clock,
    FundingSourceAdministrationRepository,
    IdGenerator,
)
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    DomainError,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    WalletAvailability,
    WalletEntry,
    WalletEntryType,
    WalletError,
    wallet_availability,
)
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger(__name__)

CREATE_FUNDING_SOURCE_COMMAND = "create_funding_source"
RENAME_FUNDING_SOURCE_COMMAND = "rename_funding_source"
ARCHIVE_FUNDING_SOURCE_COMMAND = "archive_funding_source"
SET_DEFAULT_FUNDING_SOURCE_COMMAND = "set_default_funding_source"
ADD_WALLET_ENTRY_COMMAND = "add_wallet_entry"


@dataclass(frozen=True, slots=True)
class ManagedFundingSource:
    """One source plus the authoritative wallet facts it owns, when tracked.

    Attributes:
        source: Stable source display and lifecycle state.
        entries: Immutable ledger history; empty for untracked sources.
        availability: Expiry-aware wallet projection, or None when untracked.
    """

    source: FundingSource
    entries: tuple[WalletEntry, ...]
    availability: WalletAvailability | None


@dataclass(frozen=True, slots=True)
class FundingSourceManagement:
    """Complete owner-scoped funding-source and wallet administration view.

    Attributes:
        sources: Active and archived sources in repository order.
        observed_at: Instant used for every expiry-aware wallet projection.
    """

    sources: tuple[ManagedFundingSource, ...]
    observed_at: datetime


def funding_source_request_hash(
    *,
    profile_id: UUID,
    source_id: UUID | None,
    name: str | None = None,
    funding_class: FundingClass | None = None,
    balance_mode: BalanceMode | None = None,
    is_default: bool | None = None,
) -> str:
    """Hash one source command without generated IDs or optimistic versions."""

    canonical = json.dumps(
        {
            "balance_mode": None if balance_mode is None else balance_mode.value,
            "funding_class": None if funding_class is None else funding_class.value,
            "is_default": is_default,
            "name": None if name is None else " ".join(name.strip().split()),
            "profile_id": str(profile_id),
            "source_id": None if source_id is None else str(source_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def wallet_entry_request_hash(entry: WalletEntry) -> str:
    """Hash the complete semantics of one immutable wallet command.

    The generated entry identifier is excluded so a retry may construct a new
    candidate and still recover the first stored row.
    """

    canonical = json.dumps(
        {
            "amount": [entry.amount.minor_units, entry.amount.currency],
            "entry_type": entry.entry_type.value,
            "expires_on": (
                None if entry.expires_on is None else entry.expires_on.isoformat()
            ),
            "funding_source_id": str(entry.funding_source_id),
            "occurred_at": entry.occurred_at.astimezone(UTC).isoformat(),
            "profile_id": str(entry.profile_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FundingSourceAdministrationService:
    """Manage funding labels and wallets without rewriting financial history."""

    def __init__(
        self,
        repository: FundingSourceAdministrationRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind the use cases to one transactional persistence boundary."""

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def management(self, *, profile_id: UUID) -> FundingSourceManagement:
        """Return all sources and expiry-aware wallet projections."""

        observed_at = self._clock.now()
        managed: list[ManagedFundingSource] = []
        for source in self._repository.get_funding_sources(profile_id):
            if source.balance_mode is BalanceMode.UNTRACKED:
                managed.append(ManagedFundingSource(source, (), None))
                continue
            entries = self._repository.get_wallet_entries(profile_id, source.id)
            managed.append(
                ManagedFundingSource(
                    source,
                    entries,
                    wallet_availability(source, entries, as_of=observed_at),
                )
            )
        return FundingSourceManagement(tuple(managed), observed_at)

    def create(
        self,
        source: FundingSource,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Create or replay one source with immutable class and balance mode."""

        request_hash = funding_source_request_hash(
            profile_id=source.profile_id,
            source_id=None,
            name=source.name,
            funding_class=source.funding_class,
            balance_mode=source.balance_mode,
            is_default=source.is_default,
        )
        replayed = self._repository.get_idempotent_funding_source_command(
            profile_id=source.profile_id,
            idempotency_key=idempotency_key,
            command_type=CREATE_FUNDING_SOURCE_COMMAND,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        if source.version != 1 or source.status is not FundingSourceStatus.ACTIVE:
            raise ValueError("new funding source must be active at version 1")
        stored = self._repository.save_funding_source(
            source,
            expected_version=None,
            idempotency_key=idempotency_key,
            command_type=CREATE_FUNDING_SOURCE_COMMAND,
            request_hash=request_hash,
            audit_event=self._audit(
                source, action="create", actor=actor, reason=reason
            ),
        )
        _LOGGER.info(
            "funding_source.created",
            profile_id=stored.profile_id,
            funding_source_id=stored.id,
            funding_class=stored.funding_class.value,
            balance_mode=stored.balance_mode.value,
            is_default=stored.is_default,
            version=stored.version,
        )
        return stored

    def rename(
        self,
        *,
        profile_id: UUID,
        source_id: UUID,
        expected_version: int,
        name: str,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Rename one active source under optimistic concurrency."""

        request_hash = funding_source_request_hash(
            profile_id=profile_id, source_id=source_id, name=name
        )
        replayed = self._replayed(
            profile_id, idempotency_key, RENAME_FUNDING_SOURCE_COMMAND, request_hash
        )
        if replayed is not None:
            return replayed
        current = self._current(profile_id, source_id)
        if current.status is not FundingSourceStatus.ACTIVE:
            raise DomainError("only an active funding source may be renamed")
        updated = replace(
            current,
            name=" ".join(name.strip().split()),
            version=current.version + 1,
        )
        self._save_replacement(
            current,
            updated,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=RENAME_FUNDING_SOURCE_COMMAND,
            request_hash=request_hash,
            action="rename",
            actor=actor,
            reason=reason,
        )
        return updated

    def archive(
        self,
        *,
        profile_id: UUID,
        source_id: UUID,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Archive a non-default source only after a tracked balance reaches zero."""

        request_hash = funding_source_request_hash(
            profile_id=profile_id, source_id=source_id
        )
        replayed = self._replayed(
            profile_id, idempotency_key, ARCHIVE_FUNDING_SOURCE_COMMAND, request_hash
        )
        if replayed is not None:
            return replayed
        current = self._current(profile_id, source_id)
        if current.status is not FundingSourceStatus.ACTIVE:
            raise DomainError("only an active funding source may be archived")
        if current.is_default:
            raise DomainError("default funding source cannot be archived")
        if current.balance_mode is BalanceMode.LEDGER:
            entries = self._repository.get_wallet_entries(profile_id, source_id)
            availability = wallet_availability(
                current, entries, as_of=self._clock.now()
            )
            if (
                availability.booked_balance.minor_units != 0
                or availability.available_balance.minor_units != 0
            ):
                raise WalletError("wallet balance must be zero before archive")
        updated = replace(
            current,
            status=FundingSourceStatus.ARCHIVED,
            version=current.version + 1,
        )
        self._save_replacement(
            current,
            updated,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=ARCHIVE_FUNDING_SOURCE_COMMAND,
            request_hash=request_hash,
            action="archive",
            actor=actor,
            reason=reason,
        )
        return updated

    def set_default(
        self,
        *,
        profile_id: UUID,
        source_id: UUID,
        expected_version: int,
        expected_previous_default_id: UUID | None,
        expected_previous_default_version: int | None,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Move the profile default between active sources in one transaction."""

        request_hash = funding_source_request_hash(
            profile_id=profile_id, source_id=source_id, is_default=True
        )
        replayed = self._replayed(
            profile_id,
            idempotency_key,
            SET_DEFAULT_FUNDING_SOURCE_COMMAND,
            request_hash,
        )
        if replayed is not None:
            return replayed
        sources = self._repository.get_funding_sources(profile_id)
        by_id = {source.id: source for source in sources}
        try:
            current = by_id[source_id]
        except KeyError as error:
            raise AggregateNotFoundError(
                "funding source does not exist in profile"
            ) from error
        if current.status is not FundingSourceStatus.ACTIVE:
            raise DomainError("default funding source must be active")
        previous = next((source for source in sources if source.is_default), None)
        if previous is not None and previous.id == current.id:
            raise DomainError("funding source is already the default")
        if (None if previous is None else previous.id) != expected_previous_default_id:
            raise OptimisticConcurrencyError("default funding source changed")
        if (
            None if previous is None else previous.version
        ) != expected_previous_default_version:
            raise OptimisticConcurrencyError(
                "displayed default funding source version changed"
            )
        updated = replace(current, is_default=True, version=current.version + 1)
        prior_updated = (
            None
            if previous is None
            else replace(previous, is_default=False, version=previous.version + 1)
        )
        audits = [
            self._audit(
                updated,
                action="set_default",
                actor=actor,
                reason=reason,
                prior_version=current.version,
            )
        ]
        if prior_updated is not None:
            audits.append(
                self._audit(
                    prior_updated,
                    action="unset_default",
                    actor=actor,
                    reason=reason,
                    prior_version=prior_updated.version - 1,
                )
            )
        self._repository.set_default_funding_source(
            updated,
            previous=prior_updated,
            expected_version=expected_version,
            expected_previous_version=expected_previous_default_version,
            idempotency_key=idempotency_key,
            command_type=SET_DEFAULT_FUNDING_SOURCE_COMMAND,
            request_hash=request_hash,
            audit_events=tuple(audits),
        )
        _LOGGER.info(
            "funding_source.default_changed",
            profile_id=profile_id,
            funding_source_id=source_id,
            previous_funding_source_id=expected_previous_default_id,
            version=updated.version,
        )
        return updated

    def add_wallet_entry(
        self,
        entry: WalletEntry,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> WalletEntry:
        """Append or replay one immutable balance-safe wallet entry."""

        request_hash = wallet_entry_request_hash(entry)
        replayed = self._repository.get_idempotent_wallet_command(
            profile_id=entry.profile_id,
            idempotency_key=idempotency_key,
            command_type=ADD_WALLET_ENTRY_COMMAND,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        existing = self._repository.get_wallet_entries(
            entry.profile_id, entry.funding_source_id
        )
        if entry.entry_type is WalletEntryType.OPENING and existing:
            raise WalletError("opening balance is allowed only as the first entry")
        occurred_at = self._clock.now()
        if entry.occurred_at > occurred_at:
            raise WalletError("wallet entry cannot occur in the future")
        audit = AuditEvent(
            id=self._ids.new(),
            profile_id=entry.profile_id,
            aggregate_type="wallet_entry",
            aggregate_id=entry.id,
            action="create",
            actor=actor,
            source_reference=f"wallet:{entry.funding_source_id}",
            occurred_at=occurred_at,
            prior_version=None,
            current_version=1,
            reason=reason,
        )
        stored = self._repository.append_wallet_entry_idempotently(
            entry,
            idempotency_key=idempotency_key,
            command_type=ADD_WALLET_ENTRY_COMMAND,
            request_hash=request_hash,
            audit_event=audit,
        )
        _LOGGER.info(
            "wallet_entry.appended",
            profile_id=entry.profile_id,
            wallet_entry_id=stored.id,
            funding_source_id=entry.funding_source_id,
            entry_type=entry.entry_type.value,
        )
        return stored

    def _replayed(
        self,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> FundingSource | None:
        """Return an identical prior source command result when claimed."""

        return self._repository.get_idempotent_funding_source_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            request_hash=request_hash,
        )

    def _current(self, profile_id: UUID, source_id: UUID) -> FundingSource:
        """Load one source inside its trusted profile scope."""

        for source in self._repository.get_funding_sources(profile_id):
            if source.id == source_id:
                return source
        raise AggregateNotFoundError("funding source does not exist in profile")

    def _save_replacement(
        self,
        current: FundingSource,
        updated: FundingSource,
        *,
        expected_version: int,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        action: str,
        actor: str,
        reason: str,
    ) -> None:
        """Persist one source version and matching audit atomically."""

        self._repository.save_funding_source(
            updated,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=command_type,
            request_hash=request_hash,
            audit_event=self._audit(
                updated,
                action=action,
                actor=actor,
                reason=reason,
                prior_version=current.version,
            ),
        )
        fields = {
            "profile_id": updated.profile_id,
            "funding_source_id": updated.id,
            "prior_version": current.version,
            "version": updated.version,
        }
        if action == "rename":
            _LOGGER.info("funding_source.renamed", **fields)
        else:
            _LOGGER.info("funding_source.archived", **fields)

    def _audit(
        self,
        source: FundingSource,
        *,
        action: str,
        actor: str,
        reason: str,
        prior_version: int | None = None,
    ) -> AuditEvent:
        """Build one content-free funding-source audit event."""

        return AuditEvent(
            id=self._ids.new(),
            profile_id=source.profile_id,
            aggregate_type="funding_source",
            aggregate_id=source.id,
            action=action,
            actor=actor,
            source_reference=f"funding-source:{source.id}",
            occurred_at=self._clock.now(),
            prior_version=prior_version,
            current_version=source.version,
            reason=reason,
        )


__all__ = [
    "ADD_WALLET_ENTRY_COMMAND",
    "ARCHIVE_FUNDING_SOURCE_COMMAND",
    "CREATE_FUNDING_SOURCE_COMMAND",
    "RENAME_FUNDING_SOURCE_COMMAND",
    "SET_DEFAULT_FUNDING_SOURCE_COMMAND",
    "FundingSourceAdministrationService",
    "FundingSourceManagement",
    "ManagedFundingSource",
    "funding_source_request_hash",
    "wallet_entry_request_hash",
]
