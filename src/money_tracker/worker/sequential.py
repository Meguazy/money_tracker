"""One durable inbox item at a time, from claim to committed response.

AD-018 splits Step 4 processing into two phases that cannot share a
transaction, because dispatch talks to a local model and to independently
connected application services. This module owns that protocol: it leases the
oldest eligible inbox row, translates it into the transport-neutral
`ChannelMessage` the Step 3 dispatcher already accepts, and only then commits
the typed result, the single logical response, any pending-action mapping, and
inbox completion together.

The order is what makes a crash recoverable. Nothing is marked complete before
the response exists, so a process that dies mid-way re-dispatches the same
source reference; finance idempotency and workflow checkpoints return the first
mutation rather than repeating it, and the outbox logical key collapses the
second render onto the first response. That is also why every identifier this
module creates is derived from the inbox row instead of generated: a fresh UUID
would make the recovered response a different one, and the idempotent insert
would refuse it.

The worker holds no Bot API token and imports no transport type. The only
value crossing into workflow code is the normalized message; the only value
crossing back out is the typed outcome.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langchain_core.exceptions import ModelConnectionError

from money_tracker.application import (
    AggregateNotFoundError,
    CategoryReviewService,
    OptimisticConcurrencyError,
)
from money_tracker.domain import (
    CategoryProposal,
    DomainError,
    Expense,
    FundingSource,
    Taxonomy,
)
from money_tracker.messaging import (
    ControlRevocationDraft,
    InboxClaim,
    MessagingRepository,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
    PendingAction,
    PendingActionDraft,
    PendingActionTarget,
    RetryPolicy,
)
from money_tracker.persistence.messaging import (
    MessagingConflictError,
    MessagingLeaseError,
)
from money_tracker.presentation import (
    MovementView,
    OwnerResponse,
    ResponseKind,
    TelegramResponseRenderer,
    action_for_code,
)
from money_tracker.runtime.review_graph import review_question
from money_tracker.workflows import (
    CallbackAction,
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    DispatchStatus,
    MovementChangeReason,
    PendingMovementAction,
    RoutingContext,
    StructuredOutputError,
    outcome_facts,
)

_LOGGER = logging.getLogger("money_tracker.worker")
_CALLBACK = re.compile(
    r"^a:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):([a-z][a-z0-9_]{0,15})$"
)
_MOVEMENT_STATUSES = frozenset(
    {
        DispatchStatus.DRAFTED,
        DispatchStatus.APPROVED,
        DispatchStatus.REUSED,
        DispatchStatus.CORRECTED,
        DispatchStatus.POSTED,
        DispatchStatus.VOIDED,
    }
)


class Clock(Protocol):
    """Aware clock boundary shared by the durable messaging services."""

    def now(self) -> datetime:
        """Return an aware current instant."""


class WorkflowDispatchPort(Protocol):
    """The single workflow entry point AD-016 exposes to a channel.

    The worker depends on the call rather than on the compiled assembly, so
    composition stays free to rebuild the graphs between work units. It has to
    be free to: the read-selection policy is given the active category and
    funding-source names when the graphs are built, and an approved review adds
    a category the very next message may ask about.
    """

    def dispatch(
        self,
        message: ChannelMessage,
        *,
        context: DispatchContext,
    ) -> DispatchOutcome:
        """Route one normalized work unit and return its typed outcome."""


class WorkerConfigurationReader(Protocol):
    """Trusted finance reads one work unit needs around dispatch.

    These are reads only. Every mutation stays inside the specialist the
    dispatcher invokes, so the worker cannot become a second write path.
    """

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Return the movement a rendered summary reports."""

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the taxonomy that names allocated categories."""

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the sources that name payment allocations."""

    def get_category_proposal(
        self, profile_id: UUID, proposal_id: UUID
    ) -> CategoryProposal:
        """Return the candidate a paused review is showing."""

    def get_pending_category_proposals(
        self, profile_id: UUID
    ) -> tuple[CategoryProposal, ...]:
        """Return every review the owner may still answer."""

    def get_latest_correctable_movement(self, profile_id: UUID) -> UUID | None:
        """Return the movement an uncorrelated correction would target."""


@dataclass(frozen=True, slots=True)
class _Correlation:
    """What a callback or reply resolved before it could enter dispatch."""

    action: PendingAction | None = None
    pending_action_id: UUID | None = None
    reply_to_action_id: UUID | None = None
    movement_actions: tuple[PendingMovementAction, ...] = ()
    latest_movement_id: UUID | None = None


class SequentialWorker:
    """Process the oldest eligible inbox item and commit its response.

    Attributes:
        The worker keeps no correctness state between calls. Ordering, leases,
        attempts, and results live in PostgreSQL, so restarting the process
        resumes exactly where the durable state says it stopped.
    """

    def __init__(
        self,
        *,
        bot_key: str,
        repository: MessagingRepository,
        configuration: WorkerConfigurationReader,
        review: CategoryReviewService,
        dispatcher: WorkflowDispatchPort,
        renderer: TelegramResponseRenderer,
        clock: Clock,
        retry_policy: RetryPolicy | None = None,
        lease: timedelta = timedelta(minutes=5),
    ) -> None:
        """Assemble one worker over durable state and the Step 3 dispatcher.

        Args:
            bot_key: Non-secret bot and environment persistence scope.
            repository: Owner of inbox order, leases, outbox, and correlations.
            configuration: Trusted finance reads for routing and presentation.
            review: Service supplying the AD-008 candidate-loop bound.
            dispatcher: The single transport-neutral workflow entry point.
            renderer: Owner-facing presentation for typed outcomes.
            clock: Aware runtime clock.
            retry_policy: Shared bounded five-attempt schedule.
            lease: Bound on how long one claimed item may be held. It must
                exceed the slowest realistic dispatch, because an expired lease
                lets another worker claim work that is still running.
        """

        self._bot_key = bot_key
        self._repository = repository
        self._configuration = configuration
        self._review = review
        self._dispatcher = dispatcher
        self._renderer = renderer
        self._clock = clock
        self._retry_policy = retry_policy or RetryPolicy()
        self._lease = lease

    def process_once(self) -> bool:
        """Claim and finalize at most one inbox item.

        Returns:
            True when an item was claimed, whatever terminal state it reached.
            False means the head of the queue is empty, leased, or waiting for
            its retry time, which are all reasons to idle rather than to spin.
        """

        now = self._clock.now()
        claim = self._repository.claim_next_inbox(
            self._bot_key,
            now=now,
            lease=self._lease,
            claim_token=uuid4(),
        )
        if claim is None:
            return False
        try:
            self._process(claim)
        except MessagingLeaseError:
            # The lease expired while dispatch was running, so another claim
            # already owns this item. Failing it here would overwrite whatever
            # that claim is doing; the durable state is already correct.
            _LOGGER.warning("Worker lost its inbox lease before completing")
        except (ModelConnectionError, OptimisticConcurrencyError) as error:
            self._fail(claim, _transient_code(error), transient=True)
        except (
            AggregateNotFoundError,
            DomainError,
            MessagingConflictError,
            StructuredOutputError,
            ValueError,
        ) as error:
            self._fail(claim, _permanent_code(error), transient=False)
        return True

    def _process(self, claim: InboxClaim) -> None:
        """Dispatch one claimed item and commit everything it produced."""

        binding = self._repository.owner_binding(self._bot_key)
        if binding is None:
            raise ValueError("the bot has no owner binding for accepted work")
        profile_id = binding.profile_id
        correlation = self._correlate(claim, profile_id)
        message = self._message(claim, correlation)
        outcome = self._dispatcher.dispatch(
            message,
            context=DispatchContext(
                profile_id=profile_id,
                routing=RoutingContext(
                    pending_category_review_ids=tuple(
                        proposal.id
                        for proposal in (
                            self._configuration.get_pending_category_proposals(
                                profile_id
                            )
                        )
                    ),
                    latest_correctable_movement_id=correlation.latest_movement_id,
                ),
                movement_actions=correlation.movement_actions,
            ),
        )
        response = self._render(outcome, claim, profile_id)
        self._commit(
            claim, profile_id, binding.telegram_user_id, outcome, response, correlation
        )

    def _correlate(self, claim: InboxClaim, profile_id: UUID) -> _Correlation:
        """Resolve the trusted target a callback or reply is allowed to reach.

        An unresolved callback keeps its branch but loses its correlation, so
        the router clarifies it instead of applying the owner's last decision
        to whatever happens to be pending now.
        """

        update = claim.update
        latest = self._configuration.get_latest_correctable_movement(profile_id)
        action: PendingAction | None = None
        if update.callback_action is not None:
            match = _CALLBACK.fullmatch(update.callback_action)
            if match is None:
                raise ValueError("stored callback data is not a supported control")
            action = self._repository.resolve_callback_action(
                bot_key=self._bot_key,
                telegram_user_id=update.telegram_user_id,
                telegram_chat_id=update.telegram_chat_id,
                action_id=UUID(match.group(1)),
                action=match.group(2),
            )
        elif update.reply_to_message_id is not None:
            action = self._repository.resolve_reply_action(
                bot_key=self._bot_key,
                telegram_user_id=update.telegram_user_id,
                telegram_chat_id=update.telegram_chat_id,
                telegram_message_id=update.reply_to_message_id,
            )
        if action is None:
            return _Correlation(latest_movement_id=latest)
        replied = update.callback_action is None
        if action.target_type is PendingActionTarget.CATEGORY_REVIEW:
            return _Correlation(
                action=action,
                pending_action_id=None if replied else action.target_id,
                reply_to_action_id=action.target_id if replied else None,
                latest_movement_id=latest,
            )
        return _Correlation(
            action=action,
            pending_action_id=None if replied else action.id,
            movement_actions=(PendingMovementAction(action.id, action.target_id),),
            # A reply names its movement, so it is the one this work unit may
            # correct even when a newer movement exists.
            latest_movement_id=action.target_id if replied else latest,
        )

    def _message(self, claim: InboxClaim, correlation: _Correlation) -> ChannelMessage:
        """Translate the stored row into the transport-neutral work unit."""

        update = claim.update
        callback = None
        if update.callback_action is not None:
            match = _CALLBACK.fullmatch(update.callback_action)
            callback = None if match is None else action_for_code(match.group(2))
            if callback is None:
                raise ValueError("stored callback names no supported branch")
        return ChannelMessage(
            source_reference=update.source_reference,
            received_at=update.received_at,
            text=update.text,
            callback_action=callback,
            pending_action_id=correlation.pending_action_id,
            reply_to_action_id=correlation.reply_to_action_id,
            has_image=update.has_image,
        )

    def _render(
        self,
        outcome: DispatchOutcome,
        claim: InboxClaim,
        profile_id: UUID,
    ) -> OwnerResponse:
        """Read the display data this outcome needs and render one response."""

        movement = None
        question = None
        if outcome.status in _MOVEMENT_STATUSES and outcome.expense_id is not None:
            movement = self._movement_view(profile_id, outcome.expense_id)
        if (
            outcome.status is DispatchStatus.AWAITING_CATEGORY_APPROVAL
            and outcome.proposal_id is not None
        ):
            question = review_question(
                self._configuration,
                self._review,
                profile_id=profile_id,
                proposal_id=outcome.proposal_id,
            )
        return self._renderer.render(
            outcome,
            action_id=_action_id(claim.update.id),
            movement=movement,
            question=question,
        )

    def _movement_view(self, profile_id: UUID, expense_id: UUID) -> MovementView:
        """Name the stored movement's purpose and payment for the owner."""

        expense = self._configuration.get_expense(profile_id, expense_id)
        taxonomy = self._configuration.get_taxonomy(profile_id)
        sources = {
            source.id: source.name
            for source in self._configuration.get_funding_sources(profile_id)
        }
        return MovementView(
            amount=expense.amount,
            occurred_at=expense.occurred_at,
            description=expense.description,
            status=expense.status,
            category_names=tuple(
                taxonomy.category(allocation.category_id).name
                for allocation in expense.category_allocations
            ),
            funding_source_names=tuple(
                sources[allocation.funding_source_id]
                for allocation in expense.payment_allocations
                if allocation.funding_source_id in sources
            ),
        )

    def _commit(
        self,
        claim: InboxClaim,
        profile_id: UUID,
        telegram_user_id: int,
        outcome: DispatchOutcome,
        response: OwnerResponse,
        correlation: _Correlation,
    ) -> None:
        """Persist result, response, correlation, and completion together."""

        update = claim.update
        draft = self._response_draft(claim, response)
        response_id = draft.id
        pending_action = None
        revocations: tuple[ControlRevocationDraft, ...] = ()
        if outcome.status in _MOVEMENT_STATUSES and outcome.expense_id is not None:
            revocations = self._revocations(
                update=update,
                movement_id=outcome.expense_id,
                response_id=response_id,
            )
        if response.target is not None:
            pending_action = PendingActionDraft(
                id=_action_id(update.id),
                bot_key=update.bot_key,
                profile_id=profile_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=update.telegram_chat_id,
                outbox_id=response_id,
                target_type=response.target.target_type,
                target_id=response.target.target_id,
                allowed_actions=response.target.allowed_actions,
            )
        elif _needs_modification_reply(
            callback_data=update.callback_action,
            correlation=correlation,
            outcome=outcome,
        ):
            assert correlation.action is not None
            pending_action = PendingActionDraft(
                id=_action_id(update.id),
                bot_key=update.bot_key,
                profile_id=profile_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=update.telegram_chat_id,
                outbox_id=response_id,
                target_type=PendingActionTarget.MOVEMENT,
                target_id=correlation.action.target_id,
                allowed_actions=("modify",),
            )
        self._repository.complete_inbox(
            claim,
            workflow_status=outcome.status.value,
            workflow_result=outcome_facts(outcome),
            response=draft,
            pending_action=pending_action,
            consumed_action_id=_consumed(correlation, outcome),
            now=self._clock.now(),
            revocations=revocations,
        )
        _LOGGER.info(
            "Worker committed work unit outcome=%s response=%s",
            outcome.status.value,
            response.kind.value,
        )

    def _revocations(
        self,
        *,
        update: NormalizedTelegramUpdate,
        movement_id: UUID,
        response_id: UUID,
    ) -> tuple[ControlRevocationDraft, ...]:
        """Retire the controls on every earlier summary of one movement.

        Any lifecycle outcome makes the buttons on earlier summaries wrong
        rather than merely old, so this keys off the outcome rather than off
        the new response carrying controls of its own. Ending a movement is
        exactly the case that produces no new controls, and leaving the old
        ones is worst there: a cancelled movement kept offering `Registra`.

        A clarification deliberately leaves the previous controls alone,
        because it asks the owner to use them, which is the same reason
        `_consumed` spares its mapping.

        Args:
            update: Normalized inbound item this response answers.
            movement_id: Movement the new summary describes.
            response_id: Response that supersedes the earlier ones.

        Returns:
            One replay-stable revocation per superseded delivered summary.
        """

        superseded = self._repository.delivered_control_messages(
            bot_key=update.bot_key,
            movement_id=movement_id,
            excluding_outbox_id=response_id,
        )
        return tuple(
            ControlRevocationDraft(
                id=uuid5(
                    NAMESPACE_URL,
                    f"money-tracker:revoke:{update.id}:{message_id}",
                ),
                bot_key=update.bot_key,
                telegram_chat_id=update.telegram_chat_id,
                logical_key=f"inbox:{update.id}:revoke:{message_id}",
                target_message_id=message_id,
                content_hash=hashlib.sha256(
                    f"revoke:{update.telegram_chat_id}:{message_id}".encode()
                ).hexdigest(),
                inbox_id=update.id,
            )
            for message_id in superseded
        )

    @staticmethod
    def _response_draft(
        claim: InboxClaim, response: OwnerResponse
    ) -> OutboxMessageDraft:
        """Give one rendered response its replay-stable durable identity."""

        update = claim.update
        return OutboxMessageDraft(
            id=_response_id(update.id, response.kind),
            bot_key=update.bot_key,
            telegram_chat_id=update.telegram_chat_id,
            logical_key=f"inbox:{update.id}:response:{response.kind.value}",
            response_kind=response.kind.value,
            text=response.text,
            content_hash=response.content_hash,
            buttons=response.buttons,
            inbox_id=update.id,
            # A callback answers a message the bot itself sent, so only owner
            # text is worth threading a reply onto.
            reply_to_message_id=(
                update.telegram_message_id if update.callback_action is None else None
            ),
        )

    def _fail(self, claim: InboxClaim, error_code: str, *, transient: bool) -> None:
        """Schedule the bounded retry, or make the item a visible dead letter."""

        now = self._clock.now()
        terminal = not transient or self._retry_policy.exhausted(claim.attempt_count)
        retry_at = None
        if not terminal:
            retry_at = self._retry_policy.retry_at(
                now, attempt_count=claim.attempt_count
            )
        response = self._renderer.failure(terminal=terminal)
        self._repository.fail_inbox(
            claim,
            error_code=error_code,
            transient=transient,
            retry_at=retry_at,
            response=self._response_draft(claim, response),
            now=now,
        )
        _LOGGER.warning(
            "Worker failed a work unit reason=%s transient=%s attempt=%d",
            error_code,
            transient,
            claim.attempt_count,
        )


def _consumed(correlation: _Correlation, outcome: DispatchOutcome) -> UUID | None:
    """Decide whether the resolved correlation is spent.

    A clarification asks the owner to try again, so the controls they already
    have must keep working; consuming the mapping would leave the previous
    message inert and the review unanswerable by button. Every other outcome
    either finished the target or replaced its controls with new ones.
    """

    if correlation.action is None:
        return None
    if outcome.status is DispatchStatus.CLARIFICATION:
        return None
    return correlation.action.id


def _needs_modification_reply(
    *,
    callback_data: str | None,
    correlation: _Correlation,
    outcome: DispatchOutcome,
) -> bool:
    """Decide whether a clarification must remain tied to one movement.

    The first Modify press contains no replacement facts, so the movement graph
    asks for them. The clarification becomes the message the owner naturally
    replies to; persisting a new mapping for that response keeps the selected
    movement stable across restarts and across any newer writes.

    Args:
        callback_data: Stored bounded control value from the claimed work item.
        correlation: Trusted pending action resolved before dispatch.
        outcome: Typed dispatcher result to be rendered and committed.

    Returns:
        True only for the expected first-step modification clarification.
    """

    if (
        callback_data is None
        or correlation.action is None
        or correlation.action.target_type is not PendingActionTarget.MOVEMENT
        or outcome.status is not DispatchStatus.CLARIFICATION
        or outcome.clarification_reason
        != MovementChangeReason.CORRECTION_DETAILS_REQUIRED.value
    ):
        return False
    match = _CALLBACK.fullmatch(callback_data)
    return (
        match is not None
        and action_for_code(match.group(2)) is CallbackAction.MODIFY_EXPENSE
    )


def _action_id(inbox_id: UUID) -> UUID:
    """Derive the one correlation identity this inbound item may create."""

    return uuid5(NAMESPACE_URL, f"money-tracker:action:{inbox_id}")


def _response_id(inbox_id: UUID, kind: ResponseKind) -> UUID:
    """Derive the stable identity of this item's single logical response."""

    return uuid5(NAMESPACE_URL, f"money-tracker:outbox:{inbox_id}:{kind.value}")


def _transient_code(error: Exception) -> str:
    """Name a recoverable failure without leaking its message."""

    if isinstance(error, ModelConnectionError):
        return "model_unavailable"
    return "concurrent_mutation"


def _permanent_code(error: Exception) -> str:
    """Name an unrecoverable failure without leaking its message."""

    if isinstance(error, AggregateNotFoundError):
        return "referenced_record_missing"
    if isinstance(error, MessagingConflictError):
        return "messaging_state_conflict"
    if isinstance(error, StructuredOutputError):
        return "model_output_invalid"
    if isinstance(error, DomainError):
        return "domain_rule_violation"
    return "invalid_work_unit"


__all__ = [
    "SequentialWorker",
    "WorkerConfigurationReader",
    "WorkflowDispatchPort",
]
