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


CANDIDATE_EXTRACTION_INSTRUCTIONS_VERSION = "candidate-extraction-it-v1"
"""Identifier recorded by an evaluation run; not a promoted configuration."""

CANDIDATE_EXTRACTION_INSTRUCTIONS = """\
You read one message in which the owner of a personal finance assistant \
reports money they spent or money returned to them. The owner writes in \
Italian. Extract only what the message states.

Amount:
- Report the magnitude as a positive integer number of euro cents. Twelve euro \
is 1200. Twelve euro and fifty cents is 1250.
- Italian writes decimals with a comma. `12,50` is 1250 cents.
- Set polarity to refund only when money came back to the owner, and to \
expense otherwise. Never express direction with a negative amount.
- If the message states no amount you can be sure of, omit it and declare \
amount unknown.

Time:
- Copy the owner's own temporal words verbatim into the occurrence field, for \
example `ieri`, `l'altro ieri`, `il 3 marzo`, `lunedì scorso`. Do not convert \
them into a date and do not invent a date.
- If the message states no time at all, omit the field. That is not unknown: \
it means the movement just happened.
- Declare the occurrence unknown only when the message does refer to a time \
you cannot copy as a short phrase.

Funding:
- Copy the owner's own words for each payment method, for example \
`buoni pasto`, `carta`, `contanti`. Never invent a method and never choose a \
default; omitting funding entirely is correct when the message names none.
- When the owner splits a payment, list one part per method and give every \
part its own positive amount in cents. The parts must add up to the total.
- When the owner names one method without splitting, list that one part and \
omit its amount.

Description and merchant:
- Write a short Italian description of what the money was for.
- Set the merchant only when the message names a shop, brand, or business. A \
product is not a merchant.

Never state a category or an account identifier. Never state a currency: the \
assistant operates in one currency and adds it itself.

Never write a placeholder such as `unknown`, `n/a`, or `sconosciuto` as if it \
were a value: it would be recorded as the merchant's name or as a payment \
method. Use null for a field the message does not fill.

The unknowns list is not for missing information. It has one narrow use: the \
message refers to something you cannot read. Silence is not something you \
cannot read.

- `Ho pagato 25 euro di benzina` states no time and no payment method. Set \
occurrence and funding to null and leave unknowns empty. The assistant knows \
the movement just happened and which method to use.
- `Ho speso 12 euro l'altro giorno` refers to a time you cannot copy as one of \
the supported phrases. Put `occurred_at` in unknowns.
- `Ho speso 12 euro, non ricordo con cosa` refers to a payment method you \
cannot identify. Put `payment` in unknowns.
- `Ho speso un po' al bar` states no amount you can be sure of. Put `amount` in \
unknowns.

Every entry in unknowns costs the owner a question, so an empty list is the \
normal and correct answer for a clear message.
"""
"""Unpromoted candidate instructions for the expense extraction boundary."""

CANDIDATE_CATEGORY_FIT_INSTRUCTIONS_VERSION = "candidate-category-fit-it-v1"
"""Identifier recorded by an evaluation run; not a promoted configuration."""

CANDIDATE_CATEGORY_FIT_INSTRUCTIONS = """\
You decide which spending purpose describes one movement, choosing from the \
owner's active category list supplied below. Categories are named, never \
numbered: reply with a name exactly as it appears in that list.

Classify by the purpose of the spending, not by the shop that sold it and not \
by the payment method. Cat food bought at a supermarket is pet food, not \
groceries. A shop name, a brand, and a payment method are never categories.

Prefer an existing category. Choose the existing outcome whenever any active \
category adequately describes the purpose, even if a more specific one could \
be imagined. Choose the proposal outcome only when no active category is \
adequate.

For a proposal:
- Give the candidate a short Italian name describing a purpose.
- Set the parent to an existing top-level category name when the candidate \
belongs under one. Omit the parent only when no existing purpose fits.
- Explain in the rationale why no active category is adequate.
- List in considered the active categories you examined and rejected. Use \
only names from the supplied list.
- Never propose a name that already appears in the list.

Quote a short verbatim span of the owner message as evidence for whichever \
outcome you choose.
"""
"""Unpromoted candidate instructions for the category-fit boundary."""


def render_active_categories(names: tuple[str, ...]) -> str:
    """Render the owner's active category list for the category-fit boundary.

    The list is built from the profile's taxonomy at call time rather than
    written into the instructions, so a model can only name a category the
    owner actually has.

    Args:
        names: Active category display names in taxonomy order.

    Returns:
        A newline-separated list to append to the instructions.

    Raises:
        ValueError: If no active category was supplied.
    """

    if not names:
        raise ValueError("category fit requires at least one active category")
    listed = "\n".join(f"- {name}" for name in names)
    return f"\nActive categories:\n{listed}\n"
