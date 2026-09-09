"""Behavior tests for the durable claim-dispatch-commit protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.exceptions import ModelConnectionError

from money_tracker.application import AggregateNotFoundError
from money_tracker.domain import (
    Category,
    CategoryAllocation,
    CategoryProposal,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Taxonomy,
)
from money_tracker.domain.funding import BalanceMode, FundingSourceStatus
from money_tracker.messaging import (
    InboxClaim,
    InboxStatus,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
    OwnerBinding,
    PendingAction,
    PendingActionDraft,
    PendingActionStatus,
    PendingActionTarget,
)
from money_tracker.persistence.messaging import (
    MessagingConflictError,
    MessagingLeaseError,
)
from money_tracker.presentation import ResponseKind, TelegramResponseRenderer
from money_tracker.worker import SequentialWorker
from money_tracker.workflows import (
    CallbackAction,
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    DispatchStatus,
    RoutingDecision,
    RoutingReason,
    RoutingSource,
    WorkflowIntent,
)

BOT_KEY = "telegram-test"
PROFILE = UUID("00000000-0000-0000-0000-0000000000aa")
EXPENSE_ID = UUID("00000000-0000-0000-0000-0000000000bb")
PROPOSAL_ID = UUID("00000000-0000-0000-0000-0000000000cc")
CARD_ID = UUID("00000000-0000-0000-0000-0000000000dd")
CATEGORY_ID = UUID("00000000-0000-0000-0000-0000000000ee")
INBOX_ID = UUID("00000000-0000-0000-0000-0000000000ff")
NOW = datetime(2026, 9, 9, 10, tzinfo=UTC)


class _Clock:
    """Fixed aware worker clock."""

    def now(self) -> datetime:
        """Return the deterministic test instant."""

        return NOW


@dataclass
class _Configuration:
    """In-memory finance reads with the same contract as the repository."""

    latest_movement: UUID | None = EXPENSE_ID
    pending: tuple[CategoryProposal, ...] = ()
    expense_status: ExpenseStatus = ExpenseStatus.DRAFT

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Return the stored movement a summary reports."""

        return Expense(
            id=expense_id,
            profile_id=profile_id,
            amount=Money(1200),
            occurred_at=datetime(2026, 9, 9, 8, tzinfo=UTC),
            description="croccantini per i gatti",
            merchant=None,
            status=self.expense_status,
            category_allocations=(
                CategoryAllocation(
                    category_id=CATEGORY_ID,
                    amount=Money(1200),
                    provenance="owner-category-review",
                ),
            ),
            payment_allocations=(
                PaymentAllocation(funding_source_id=CARD_ID, amount=Money(1200)),
            ),
            source_reference=f"telegram:{BOT_KEY}:41",
            idempotency_key="telegram:expense:1",
        )

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return a taxonomy naming the allocated category."""

        return Taxonomy(
            (
                Category(
                    id=CATEGORY_ID,
                    name="Cibo e accessori",
                    description="Cibo e accessori per gli animali.",
                    status=CategoryStatus.ACTIVE,
                ),
            )
        )

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the source naming the payment allocation."""

        return (
            FundingSource(
                id=CARD_ID,
                profile_id=profile_id,
                name="Carta principale",
                funding_class=FundingClass.PERSONAL_FUNDS,
                balance_mode=BalanceMode.UNTRACKED,
                status=FundingSourceStatus.ACTIVE,
                is_default=True,
            ),
        )

    def get_category_proposal(
        self, profile_id: UUID, proposal_id: UUID
    ) -> CategoryProposal:
        """Return the candidate awaiting the owner."""

        return CategoryProposal(
            id=proposal_id,
            profile_id=profile_id,
            proposed_name="Giardinaggio",
            description="Attrezzi e materiali per il giardino.",
            parent_id=None,
            rationale="Nessuna categoria attiva descrive lo scopo.",
            alternative_category_ids=(),
            excluded_names=(),
            source_reference=f"telegram:{BOT_KEY}:41",
        )

    def get_pending_category_proposals(
        self, profile_id: UUID
    ) -> tuple[CategoryProposal, ...]:
        """Return the reviews the owner may still answer."""

        return self.pending

    def get_latest_correctable_movement(self, profile_id: UUID) -> UUID | None:
        """Return the default correction target."""

        return self.latest_movement


@dataclass
class _Review:
    """The single review fact the paused question needs."""

    is_exhausted: bool = False

    def exhausted(self, *, profile_id: UUID, proposal_id: UUID) -> bool:
        """Return whether the candidate loop reached its cap."""

        return self.is_exhausted


@dataclass
class _Repository:
    """Small real-behavior fake for the durable messaging boundary."""

    claim: InboxClaim | None = None
    action: PendingAction | None = None
    binding: OwnerBinding | None = None
    completions: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    resolved: list[dict[str, Any]] = field(default_factory=list)
    complete_error: Exception | None = None

    def claim_next_inbox(self, bot_key: str, **_: Any) -> InboxClaim | None:
        """Lease the configured head item exactly once."""

        claim, self.claim = self.claim, None
        return claim

    def owner_binding(self, bot_key: str) -> OwnerBinding | None:
        """Return the trusted owner scope."""

        return self.binding

    def resolve_callback_action(self, **values: Any) -> PendingAction | None:
        """Return the active mapping in the owner's own scope."""

        self.resolved.append({"kind": "callback", **values})
        return self.action

    def resolve_reply_action(self, **values: Any) -> PendingAction | None:
        """Return the mapping the replied-to message created."""

        self.resolved.append({"kind": "reply", **values})
        return self.action

    def complete_inbox(self, claim: InboxClaim, **values: Any) -> UUID:
        """Record the single terminal transaction."""

        if self.complete_error is not None:
            raise self.complete_error
        self.completions.append({"claim": claim, **values})
        response: OutboxMessageDraft = values["response"]
        return response.id

    def fail_inbox(self, claim: InboxClaim, **values: Any) -> None:
        """Record the retry or dead-letter transition."""

        self.failures.append({"claim": claim, **values})


class _Dispatcher:
    """Scripted stand-in for the model-backed workflow entry point."""

    def __init__(self, outcome: DispatchOutcome | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[ChannelMessage, DispatchContext]] = []

    def dispatch(
        self,
        message: ChannelMessage,
        *,
        context: DispatchContext,
    ) -> DispatchOutcome:
        """Record the translated work unit and return the scripted result."""

        self.calls.append((message, context))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _update(**overrides: Any) -> NormalizedTelegramUpdate:
    """Build one accepted normalized inbox row."""

    fields: dict[str, Any] = {
        "id": INBOX_ID,
        "bot_key": BOT_KEY,
        "update_id": 41,
        "telegram_user_id": 1001,
        "telegram_chat_id": 1001,
        "received_at": NOW,
        "accepted_at": NOW,
        "telegram_message_id": 7,
        "text": "ho speso 12 euro per i croccantini dei gatti",
    }
    fields.update(overrides)
    return NormalizedTelegramUpdate(**fields)


def _claim(
    update: NormalizedTelegramUpdate | None = None,
    **overrides: Any,
) -> InboxClaim:
    """Build one leased work item."""

    fields: dict[str, Any] = {
        "update": update or _update(),
        "status": InboxStatus.PROCESSING,
        "attempt_count": 1,
        "replay_count": 0,
        "claim_token": uuid4(),
        "claimed_until": NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return InboxClaim(**fields)


def _pending_action(
    target_type: PendingActionTarget = PendingActionTarget.CATEGORY_REVIEW,
    target_id: UUID = PROPOSAL_ID,
) -> PendingAction:
    """Build one active correlation the owner may answer."""

    return PendingAction(
        id=UUID("00000000-0000-0000-0000-000000000011"),
        bot_key=BOT_KEY,
        profile_id=PROFILE,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        outbox_id=uuid4(),
        target_type=target_type,
        target_id=target_id,
        allowed_actions=("approve", "cancel"),
        status=PendingActionStatus.ACTIVE,
        telegram_message_id=7,
        created_at=NOW,
        consumed_at=None,
    )


def _outcome(status: DispatchStatus, **overrides: Any) -> DispatchOutcome:
    """Build one typed dispatch result."""

    fields: dict[str, Any] = {
        "routing": RoutingDecision(
            intent=WorkflowIntent.EXPENSE,
            source=RoutingSource.MODEL,
            reason=RoutingReason.MODEL_CLASSIFICATION,
        ),
        "status": status,
    }
    fields.update(overrides)
    return DispatchOutcome(**fields)


def _worker(
    repository: _Repository,
    dispatcher: _Dispatcher,
    configuration: _Configuration | None = None,
    review: _Review | None = None,
) -> SequentialWorker:
    """Assemble the production worker over the test boundaries."""

    return SequentialWorker(
        bot_key=BOT_KEY,
        repository=repository,
        configuration=configuration or _Configuration(),
        review=review or _Review(),
        dispatcher=dispatcher,
        renderer=TelegramResponseRenderer(),
        clock=_Clock(),
    )


def _bound(**overrides: Any) -> _Repository:
    """Build a paired repository holding one claimable item."""

    fields: dict[str, Any] = {
        "claim": _claim(),
        "binding": OwnerBinding(BOT_KEY, PROFILE, 1001, 1001, NOW),
    }
    fields.update(overrides)
    return _Repository(**fields)


def test_an_empty_queue_is_reported_rather_than_polled_again() -> None:
    """Let the caller idle instead of spinning on an ineligible head row."""

    repository = _Repository(binding=OwnerBinding(BOT_KEY, PROFILE, 1001, 1001, NOW))
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED))

    assert _worker(repository, dispatcher).process_once() is False
    assert dispatcher.calls == []


def test_a_text_update_becomes_the_transport_neutral_work_unit() -> None:
    """Carry the stable source reference and trusted scope into dispatch."""

    repository = _bound()
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=EXPENSE_ID))

    assert _worker(repository, dispatcher).process_once() is True

    message, context = dispatcher.calls[0]
    assert message.source_reference == f"telegram:{BOT_KEY}:41"
    assert message.text == "ho speso 12 euro per i croccantini dei gatti"
    assert message.callback_action is None
    assert context.profile_id == PROFILE
    assert context.routing.latest_correctable_movement_id == EXPENSE_ID


def test_a_stored_movement_commits_its_response_and_correlation() -> None:
    """Commit typed result, one logical response, and its controls together."""

    repository = _bound()
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=EXPENSE_ID))

    _worker(repository, dispatcher).process_once()

    committed = repository.completions[0]
    response: OutboxMessageDraft = committed["response"]
    action: PendingActionDraft = committed["pending_action"]
    assert committed["workflow_status"] == "drafted"
    assert committed["workflow_result"]["expense_id"] == str(EXPENSE_ID)
    assert response.logical_key == f"inbox:{INBOX_ID}:response:movement"
    assert response.response_kind == ResponseKind.MOVEMENT.value
    assert "12,00 € · croccantini per i gatti" in response.text
    assert "Fonte: Carta principale" in response.text
    assert response.reply_to_message_id == 7
    assert action.target_type is PendingActionTarget.MOVEMENT
    assert action.target_id == EXPENSE_ID
    assert action.outbox_id == response.id
    assert all(
        button.callback_data.startswith(f"a:{action.id}:")
        for button in response.buttons
    )
    assert repository.failures == []


def test_a_paused_review_commits_its_question_and_branches() -> None:
    """Turn the paused candidate into controls the owner can answer."""

    repository = _bound()
    dispatcher = _Dispatcher(
        _outcome(
            DispatchStatus.AWAITING_CATEGORY_APPROVAL,
            expense_id=EXPENSE_ID,
            proposal_id=PROPOSAL_ID,
        )
    )

    _worker(repository, dispatcher).process_once()

    committed = repository.completions[0]
    action: PendingActionDraft = committed["pending_action"]
    assert committed["response"].response_kind == ResponseKind.CATEGORY_REVIEW.value
    assert "Proposta: Giardinaggio" in committed["response"].text
    assert action.target_type is PendingActionTarget.CATEGORY_REVIEW
    assert action.target_id == PROPOSAL_ID
    assert action.allowed_actions == ("approve", "again", "cancel")


def test_a_review_callback_reaches_its_own_proposal() -> None:
    """Resolve the branch against the trusted mapping, never against guesswork."""

    repository = _bound(
        claim=_claim(
            _update(text=None, callback_action=f"a:{_pending_action().id}:approve")
        ),
        action=_pending_action(),
    )
    dispatcher = _Dispatcher(
        _outcome(
            DispatchStatus.APPROVED,
            expense_id=EXPENSE_ID,
            settled_category_id=CATEGORY_ID,
        )
    )

    _worker(repository, dispatcher).process_once()

    message, _ = dispatcher.calls[0]
    assert message.callback_action is CallbackAction.APPROVE_CATEGORY
    assert message.pending_action_id == PROPOSAL_ID
    assert repository.resolved[0]["action"] == "approve"
    assert repository.completions[0]["consumed_action_id"] == _pending_action().id
    assert repository.completions[0]["response"].reply_to_message_id is None


def test_a_stale_callback_loses_its_correlation_but_keeps_its_branch() -> None:
    """Let routing clarify instead of applying a decision to a fresh target."""

    repository = _bound(
        claim=_claim(
            _update(text=None, callback_action=f"a:{_pending_action().id}:approve")
        ),
        action=None,
    )
    dispatcher = _Dispatcher(
        _outcome(
            DispatchStatus.CLARIFICATION,
            clarification_reason="uncorrelated_callback",
        )
    )

    _worker(repository, dispatcher).process_once()

    message, _ = dispatcher.calls[0]
    assert message.callback_action is CallbackAction.APPROVE_CATEGORY
    assert message.pending_action_id is None
    assert repository.completions[0]["consumed_action_id"] is None


def test_a_clarification_keeps_the_controls_the_owner_already_has() -> None:
    """Never leave a review unanswerable because one attempt asked a question."""

    repository = _bound(
        claim=_claim(
            _update(text=None, callback_action=f"a:{_pending_action().id}:best")
        ),
        action=_pending_action(),
    )
    dispatcher = _Dispatcher(
        _outcome(
            DispatchStatus.CLARIFICATION,
            proposal_id=PROPOSAL_ID,
            clarification_reason="no_best_existing",
        )
    )

    _worker(repository, dispatcher).process_once()

    committed = repository.completions[0]
    assert committed["consumed_action_id"] is None
    assert committed["pending_action"] is None
    assert committed["response"].response_kind == ResponseKind.CLARIFICATION.value


def test_a_reply_names_the_movement_it_answers() -> None:
    """Correct the movement the owner replied to, not merely the newest one."""

    other = UUID("00000000-0000-0000-0000-0000000000a1")
    repository = _bound(
        claim=_claim(_update(text="erano 15 euro", reply_to_message_id=7)),
        action=_pending_action(PendingActionTarget.MOVEMENT, other),
    )
    dispatcher = _Dispatcher(_outcome(DispatchStatus.CORRECTED, expense_id=other))

    _worker(repository, dispatcher).process_once()

    _, context = dispatcher.calls[0]
    assert repository.resolved[0]["kind"] == "reply"
    assert context.routing.latest_correctable_movement_id == other
    assert context.movement_actions[0].movement_id == other


def test_a_review_reply_correlates_to_its_proposal() -> None:
    """Let a typed answer continue the exact review it replied to."""

    repository = _bound(
        claim=_claim(_update(text="Usa la categoria Casa", reply_to_message_id=7)),
        action=_pending_action(),
    )
    dispatcher = _Dispatcher(_outcome(DispatchStatus.REUSED, expense_id=EXPENSE_ID))

    _worker(repository, dispatcher).process_once()

    message, _ = dispatcher.calls[0]
    assert message.reply_to_action_id == PROPOSAL_ID
    assert message.pending_action_id is None


def test_an_unavailable_model_is_retried_rather_than_discarded() -> None:
    """Keep accepted work when local inference is down."""

    repository = _bound()
    dispatcher = _Dispatcher(ModelConnectionError("llama.cpp is down"))

    assert _worker(repository, dispatcher).process_once() is True

    failure = repository.failures[0]
    assert failure["error_code"] == "model_unavailable"
    assert failure["transient"] is True
    assert failure["retry_at"] == NOW + timedelta(seconds=2)
    assert repository.completions == []


def test_the_last_attempt_becomes_a_visible_dead_letter() -> None:
    """Stop retrying at the bounded attempt budget instead of forever."""

    repository = _bound(claim=_claim(attempt_count=5))
    dispatcher = _Dispatcher(ModelConnectionError("llama.cpp is down"))

    _worker(repository, dispatcher).process_once()

    assert repository.failures[0]["retry_at"] is None


def test_a_missing_record_is_a_dead_letter_without_retrying() -> None:
    """Never spend five attempts on work that cannot succeed."""

    repository = _bound()
    dispatcher = _Dispatcher(AggregateNotFoundError("movement is gone"))

    _worker(repository, dispatcher).process_once()

    failure = repository.failures[0]
    assert failure["error_code"] == "referenced_record_missing"
    assert failure["transient"] is False


def test_a_conflicting_logical_response_is_a_dead_letter() -> None:
    """Refuse to overwrite a response the outbox already holds."""

    repository = _bound(complete_error=MessagingConflictError("different content"))
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=EXPENSE_ID))

    _worker(repository, dispatcher).process_once()

    assert repository.failures[0]["error_code"] == "messaging_state_conflict"


def test_a_lost_lease_leaves_the_durable_state_alone() -> None:
    """Never transition an item another claim already owns."""

    repository = _bound(complete_error=MessagingLeaseError("lease expired"))
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=EXPENSE_ID))

    assert _worker(repository, dispatcher).process_once() is True
    assert repository.failures == []
    assert repository.completions == []


def test_a_replayed_item_produces_the_same_logical_response() -> None:
    """Let a crash recovery land on the response its key already names."""

    outcome = _outcome(DispatchStatus.DRAFTED, expense_id=EXPENSE_ID)
    first = _bound()
    second = _bound(claim=_claim())

    _worker(first, _Dispatcher(outcome)).process_once()
    _worker(second, _Dispatcher(outcome)).process_once()

    original: OutboxMessageDraft = first.completions[0]["response"]
    replayed: OutboxMessageDraft = second.completions[0]["response"]
    assert original == replayed
    assert (
        first.completions[0]["pending_action"].id
        == second.completions[0]["pending_action"].id
    )


def test_work_accepted_for_an_unpaired_bot_is_refused() -> None:
    """Refuse to invent an owner scope for a binding that no longer exists."""

    repository = _Repository(claim=_claim(), binding=None)
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED))

    _worker(repository, dispatcher).process_once()

    assert repository.failures[0]["error_code"] == "invalid_work_unit"
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "callback_data",
    ["a:not-a-uuid:approve", f"a:{PROPOSAL_ID}:unknown_code"],
)
def test_unusable_stored_callback_data_is_a_dead_letter(callback_data: str) -> None:
    """Fail loudly rather than guessing which branch a bad control meant."""

    repository = _bound(
        claim=_claim(_update(text=None, callback_action=callback_data)),
        action=_pending_action(),
    )
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED))

    _worker(repository, dispatcher).process_once()

    assert repository.failures[0]["transient"] is False
    assert dispatcher.calls == []
