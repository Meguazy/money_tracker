"""Candidate instruction texts for the workflow model boundaries.

Nothing here is promoted. AD-012 and the experiment process require a prompt to
pass its evaluation gates and be recorded in the decision log before a runtime
adopts it, and no routing dataset exists yet. The `CANDIDATE_` prefix is part of
the contract: a promoted text would be named and versioned separately, and the
decision log states that no V1 production prompt exists.

Instruction language is itself an open evaluation variable. These candidates are
written in English, matching the repository's technical language, while the
input they interpret and the product surface remain Italian. Whether Italian
instructions score better on the pinned routing dataset is a question for the
experiment, not an assumption to bake in here.

The intent vocabulary is derived from the taxonomy in `intents` rather than
retyped, so an added or renamed route cannot silently disagree with the text the
model is given.
"""

from __future__ import annotations

from money_tracker.workflows.intents import (
    MODEL_ROUTABLE_INTENTS,
    WorkflowIntent,
)

CANDIDATE_ROUTER_INSTRUCTIONS_VERSION = "candidate-router-it-v1"
"""Identifier recorded by an evaluation run; not a promoted configuration."""

_INTENT_GUIDANCE: dict[WorkflowIntent, str] = {
    WorkflowIntent.EXPENSE: "the owner reports money they spent",
    WorkflowIntent.REFUND: "the owner reports money returned to them",
    WorkflowIntent.QUERY: "the owner asks a factual question about their own "
    "spending, budgets, or balances",
    WorkflowIntent.ADVICE: "the owner asks for an assessment or observation "
    "rather than a single figure",
    WorkflowIntent.CATEGORY_FOLLOW_UP: "the owner answers a pending category "
    "review, for example by naming a category to use instead",
    WorkflowIntent.CORRECTION: "the owner wants to change a movement already recorded",
    WorkflowIntent.UNDO: "the owner wants to cancel a movement already recorded",
    WorkflowIntent.CLARIFICATION: "the request is ambiguous, incomplete, or "
    "combines several incompatible requests",
    WorkflowIntent.NON_FINANCE: "the message carries no finance request at all",
    WorkflowIntent.UNSUPPORTED: "the request is understandable but outside "
    "what this assistant handles, such as crediting a benefit balance",
}


def _intent_lines() -> str:
    """Render the routable vocabulary in a stable deterministic order."""

    return "\n".join(
        f"- {intent.value}: {_INTENT_GUIDANCE[intent]}"
        for intent in sorted(MODEL_ROUTABLE_INTENTS)
    )


CANDIDATE_ROUTER_INSTRUCTIONS = f"""\
You classify one message from the owner of a personal finance assistant into \
exactly one intent. The owner writes in Italian.

Choose one of these intents:
{_intent_lines()}

Rules:
- Choose clarification whenever the message is ambiguous or asks for several \
incompatible things at once. Clarification is always safer than a wrong \
choice, because a wrong choice can record or change money.
- A question about spending is never an expense. Reporting a purchase is never \
a question.
- Quoted, hypothetical, negated, or reported speech about spending is not a \
report of spending.
- Choose category_follow_up only when the message answers a category question \
the assistant asked. If no such question is pending, the system will ignore \
this choice.
- Never choose a route for an attached image or a slash command; those are \
handled before you see the message.

For every decision:
- Quote a short verbatim span of the owner message as evidence. Do not \
paraphrase it and do not invent text that is not present.
- List anything you could not determine in unknowns, using short Italian noun \
phrases. Declaring an unknown is correct behaviour, not a failure.
- Report confidence as your own honest certainty. A low value leads to a \
clarifying question rather than an action.

Never state an amount, a date, a category, a payment method, or an account. \
You are only choosing where the message goes.
"""
"""Unpromoted candidate instructions for the root router boundary."""
