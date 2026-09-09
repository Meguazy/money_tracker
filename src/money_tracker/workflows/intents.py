"""Bounded workflow intent taxonomy and its authorization classes.

The root router selects exactly one intent for one inbound channel message.
Intents are grouped by the authority they carry so that a routing failure can
never widen the mutation surface: a read-only or non-finance message cannot be
answered by a financial write path, and a deterministic channel route cannot be
claimed by a model.
"""

from __future__ import annotations

from enum import StrEnum


class WorkflowIntent(StrEnum):
    """One supported destination of the typed root router.

    Attributes:
        EXPENSE: Record a new outgoing purchase.
        REFUND: Record a reimbursement of an earlier purchase.
        QUERY: Answer a factual spending or budget question read-only.
        ADVICE: Produce grounded observations about spending read-only.
        CATEGORY_FOLLOW_UP: Continue a paused owner category review.
        CORRECTION: Replace the editable fields of an existing movement.
        UNDO: Void an existing movement without deleting it.
        POSTING: Record a draft the owner explicitly confirmed.
        CLARIFICATION: Ask one targeted question instead of acting.
        RECEIPT: Hand an attached image to the review-only receipt workflow.
        COMMAND: Execute one supported deterministic channel command.
        NON_FINANCE: Acknowledge input that carries no finance request.
        UNSUPPORTED: Refer a recognized but out-of-scope V1 request elsewhere.
    """

    EXPENSE = "expense"
    REFUND = "refund"
    QUERY = "query"
    ADVICE = "advice"
    CATEGORY_FOLLOW_UP = "category_follow_up"
    CORRECTION = "correction"
    UNDO = "undo"
    POSTING = "posting"
    CLARIFICATION = "clarification"
    RECEIPT = "receipt"
    COMMAND = "command"
    NON_FINANCE = "non_finance"
    UNSUPPORTED = "unsupported"


MUTATING_INTENTS = frozenset(
    {
        WorkflowIntent.EXPENSE,
        WorkflowIntent.REFUND,
        WorkflowIntent.CATEGORY_FOLLOW_UP,
        WorkflowIntent.CORRECTION,
        WorkflowIntent.POSTING,
        WorkflowIntent.UNDO,
    }
)
"""Intents whose workflows may reach a finance or taxonomy mutation."""

READ_ONLY_INTENTS = frozenset(
    {
        WorkflowIntent.QUERY,
        WorkflowIntent.ADVICE,
    }
)
"""Intents whose workflows receive read-only domain tools only."""

INERT_INTENTS = frozenset(
    {
        WorkflowIntent.CLARIFICATION,
        WorkflowIntent.RECEIPT,
        WorkflowIntent.COMMAND,
        WorkflowIntent.NON_FINANCE,
        WorkflowIntent.UNSUPPORTED,
    }
)
"""Intents that neither mutate finance data nor read it through agent tools.

`RECEIPT` is inert at routing time on purpose: an image is only stored and
queued for mandatory review, and no V1 image may post a movement.
"""

DETERMINISTIC_ONLY_INTENTS = frozenset(
    {
        WorkflowIntent.RECEIPT,
        WorkflowIntent.COMMAND,
        WorkflowIntent.POSTING,
    }
)
"""Intents a model may never select.

An attached image and a supported command are recognized by deterministic
pre-routing. Letting a classification claim either would move a decision that
must be 100% reproducible into a nondeterministic component.

Posting is here for a stronger reason than reproducibility. It is the act that
makes a movement count, and AD-012 reserves that judgement for the owner or for
a gated auto-post policy. A model that could route to it would be deciding to
record money on the owner's behalf, which is exactly the authority the draft
state exists to withhold.
"""

MODEL_ROUTABLE_INTENTS = frozenset(WorkflowIntent) - DETERMINISTIC_ONLY_INTENTS
"""Intents a validated classification is allowed to request."""
