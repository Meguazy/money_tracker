"""Narrow persistence port for durable messaging application services."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from money_tracker.messaging.contracts import (
    BotEnvironment,
    DeadLetter,
    InboxClaim,
    MessagingHealth,
    NormalizedTelegramUpdate,
    OutboxClaim,
    OutboxDeadLetter,
    OutboxMessageDraft,
    OwnerBinding,
    PendingAction,
    PendingActionDraft,
    WorkflowResult,
)


class MessagingRepository(Protocol):
    """Own offsets, work queues, pairing, delivery, and correlations."""

    def ensure_bot(
        self,
        bot_key: str,
        *,
        environment: BotEnvironment,
        now: datetime,
    ) -> None:
        """Create stable offset ownership or validate an identical bot record."""

    def polling_offset(self, bot_key: str) -> int:
        """Return the next Telegram update offset owned by the gateway."""

    def commit_ingest_batch(
        self,
        *,
        bot_key: str,
        expected_offset: int,
        observed_update_ids: tuple[int, ...],
        accepted_updates: tuple[NormalizedTelegramUpdate, ...],
        rejected_count: int,
        unsupported_count: int,
        now: datetime,
    ) -> int:
        """Insert accepted updates and advance the observed offset atomically."""

    def record_empty_poll(self, bot_key: str, *, now: datetime) -> None:
        """Record transport liveness without changing acknowledgement state."""

    def claim_next_inbox(
        self,
        bot_key: str,
        *,
        now: datetime,
        lease: timedelta,
        claim_token: UUID,
    ) -> InboxClaim | None:
        """Lease only the oldest non-terminal work item when eligible."""

    def fail_inbox(
        self,
        claim: InboxClaim,
        *,
        error_code: str,
        transient: bool,
        retry_at: datetime | None,
        response: OutboxMessageDraft,
        now: datetime,
    ) -> None:
        """Commit retry or dead letter together with its logical owner notice."""

    def complete_inbox(
        self,
        claim: InboxClaim,
        *,
        workflow_status: str,
        workflow_result: WorkflowResult,
        response: OutboxMessageDraft,
        pending_action: PendingActionDraft | None,
        consumed_action_id: UUID | None,
        now: datetime,
    ) -> UUID:
        """Commit typed result, logical response, correlations, and completion."""

    def replay_inbox(self, bot_key: str, inbox_id: UUID, *, now: datetime) -> None:
        """Replay one dead letter only inside the selected bot scope."""

    def dead_letters(self, bot_key: str, *, limit: int) -> tuple[DeadLetter, ...]:
        """List failed work items an operator may inspect and replay."""

    def outbox_dead_letters(
        self, bot_key: str, *, limit: int
    ) -> tuple[OutboxDeadLetter, ...]:
        """List failed logical responses without owner content or identities."""

    def replay_outbox(self, bot_key: str, outbox_id: UUID, *, now: datetime) -> None:
        """Return one delivery dead letter to a fresh automatic attempt cycle."""

    def enqueue_notice(self, notice: OutboxMessageDraft, *, now: datetime) -> UUID:
        """Queue one owner message that no inbound work unit asked for."""

    def claim_next_outbox(
        self,
        bot_key: str,
        *,
        now: datetime,
        lease: timedelta,
        claim_token: UUID,
    ) -> OutboxClaim | None:
        """Lease the oldest eligible logical response."""

    def fail_outbox(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        transient: bool,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        """Schedule delivery retry or make the response a dead letter."""

    def complete_outbox(
        self,
        claim: OutboxClaim,
        *,
        telegram_message_id: int,
        now: datetime,
    ) -> None:
        """Persist successful delivery and reply correlation atomically."""

    def create_pairing_code(
        self,
        *,
        code_id: UUID,
        bot_key: str,
        profile_id: UUID,
        code_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Revoke an older code and store one hashed one-use code."""

    def pair_owner(
        self,
        *,
        bot_key: str,
        code_hash: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        confirmation: OutboxMessageDraft,
        now: datetime,
    ) -> OwnerBinding | None:
        """Consume a valid code and create the sole owner binding atomically.

        The code carries the profile it was generated for, so the caller does
        not supply one: a channel-side caller has no trustworthy way to know
        it, and letting it name a profile would be an authority it should not
        have. Consuming the code, creating the binding, and queueing the
        owner's confirmation commit together, so a paired owner is always told
        that they are paired.
        """

    def owner_binding(self, bot_key: str) -> OwnerBinding | None:
        """Return the trusted owner binding without exposing it to diagnostics."""

    def resolve_callback_action(
        self,
        *,
        bot_key: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        action_id: UUID,
        action: str,
    ) -> PendingAction | None:
        """Resolve an active callback only inside its owner and chat scope."""

    def resolve_reply_action(
        self,
        *,
        bot_key: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> PendingAction | None:
        """Resolve an owner reply to the exact delivered bot message."""

    def health(self, bot_key: str) -> MessagingHealth:
        """Return content-free queue, offset, action, and dead-letter state."""

    def text_delivery_latencies(
        self,
        bot_key: str,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[int, ...]:
        """Return successful text input-to-send durations in acceptance order."""
