"""Scoped read-only tool registry for the query and advice routes.

AD-006 permits a bounded agent loop only for questions and advice, and gives
those workflows read-only domain tools scoped per workflow with no generic SQL
tool. This module is that scoping made executable, and it is pure: it names the
tools, fixes their argument shapes, decides which workflow may call which, and
validates a model's request. It executes nothing.

Two rules shape everything here.

A tool is named, never described. The model chooses from a closed vocabulary
that this module derives from the registry itself, so an offered tool cannot
drift from an executable one, and a name outside the vocabulary is refused
rather than approximated.

A model never supplies a figure. It supplies a tool name, a category the owner
referred to by name, and at most a calendar month. Every number in an answer
comes from the domain read models executing against stored records, which is
what makes the grounding gate a property of the design rather than something to
measure and hope for.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import StringConstraints, model_validator

from money_tracker.domain.budgets import YearMonth
from money_tracker.domain.errors import DomainError
from money_tracker.workflows.contracts import (
    EvidenceText,
    NonEmptyText,
    _WorkflowContract,
)
from money_tracker.workflows.intents import WorkflowIntent
from money_tracker.workflows.resolution import normalize_owner_text

TOOL_CONTRACT_VERSION = "v2"
"""Version of the read-tool selection boundary.

Versioned independently of the router, extraction, and category-fit boundaries
so the tool vocabulary can grow without invalidating threads that depend only
on the others.
"""

PeriodText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
]
"""A Rome calendar month as `YYYY-MM`, the only period a tool accepts."""


class ReadTool(StrEnum):
    """One read-only domain tool a bounded agent may request.

    Every member maps to a deterministic read model over stored records. There
    is deliberately no free-form query member: AD-006 forbids a generic SQL
    tool, and a closed vocabulary is what makes an unregistered request
    detectable instead of merely unlikely.

    Attributes:
        CATEGORY_SPENDING: Posted spend rolled up under one named category for
            one month.
        BUDGET_STATUS: Target, posted spend, and signed remaining amount for
            one named category's budget in one month.
        FUNDING_CLASS_SPLIT: Posted totals for one month split into personal
            funds, benefits, and other resources.
        BUDGETS_AT_RISK: Every budget in one month ordered by how far it is
            beyond or close to its target.
    """

    CATEGORY_SPENDING = "category_spending"
    BUDGET_STATUS = "budget_status"
    FUNDING_CLASS_SPLIT = "funding_class_split"
    BUDGETS_AT_RISK = "budgets_at_risk"


CATEGORY_TOOLS = frozenset({ReadTool.CATEGORY_SPENDING, ReadTool.BUDGET_STATUS})
"""Tools that require the owner to have named a category."""

QUERY_TOOLS = frozenset(
    {
        ReadTool.CATEGORY_SPENDING,
        ReadTool.BUDGET_STATUS,
        ReadTool.FUNDING_CLASS_SPLIT,
    }
)
"""Registry scoped to the factual question route.

A question asks about one figure the owner already has in mind, so the
comparative advice tool is deliberately absent: reaching it would mean the
router chose the wrong route, and answering anyway would hide that.
"""

ADVICE_TOOLS = frozenset(
    {
        ReadTool.BUDGETS_AT_RISK,
        ReadTool.CATEGORY_SPENDING,
        ReadTool.FUNDING_CLASS_SPLIT,
    }
)
"""Registry scoped to the advice route.

J8 reports exceeded and at-risk budgets first, then material deviations, so it
needs the risk survey and the totals behind it. The single-budget lookup is
absent because the survey already covers every budget in the period.
"""

TOOL_REGISTRIES: dict[WorkflowIntent, frozenset[ReadTool]] = {
    WorkflowIntent.QUERY: QUERY_TOOLS,
    WorkflowIntent.ADVICE: ADVICE_TOOLS,
}
"""The complete scoping table, keyed by the route that owns each registry.

Every key is a read-only route. No mutating route appears, so no mutating
workflow has a registry to draw a tool from at all.
"""


def registry_for(intent: WorkflowIntent) -> frozenset[ReadTool]:
    """Return the tools one route may call.

    Args:
        intent: Route whose registry is requested.

    Returns:
        The scoped registry.

    Raises:
        DomainError: If the route owns no registry, which every mutating,
            deterministic, and inert route deliberately does not.
    """

    try:
        return TOOL_REGISTRIES[intent]
    except KeyError as error:
        raise DomainError(f"route {intent.value} has no read-tool registry") from error


class ToolRequest(_WorkflowContract):
    """One model-selected read tool awaiting deterministic validation.

    Validated as a flat schema with conditional fields rather than a
    discriminated union, matching the category-fit boundary and for the same
    reason: no local candidate is capability-screened for union output.

    The schema exposes no profile, identifier, amount, or funding source. A
    category is named and a period is a calendar month, so the widest thing a
    model can ask for is a figure about one month of the owner's own records.

    Attributes:
        tool: Requested read tool, always from the closed vocabulary.
        category_name: Category as the owner named it, required by the tools
            that roll up one category and forbidden for the others.
        period: Rome calendar month as `YYYY-MM`, or None to mean the month the
            runtime resolves as current.
        evidence: Short verbatim span of the owner message supporting the
            choice, so a reviewer can audit tool selection without the prompt.
    """

    tool: ReadTool
    category_name: NonEmptyText | None = None
    period: PeriodText | None = None
    evidence: EvidenceText

    @model_validator(mode="after")
    def _validate_arguments_match_the_tool(self) -> Self:
        """Keep every argument meaningful for the tool that received it.

        Returns:
            The validated request.

        Raises:
            ValueError: If a category tool names no category, or a tool that
                rolls up no single category names one anyway.
        """

        needs_category = self.tool in CATEGORY_TOOLS
        if needs_category and self.category_name is None:
            raise ValueError(f"{self.tool.value} requires a category name")
        if not needs_category and self.category_name is not None:
            raise ValueError(f"{self.tool.value} takes no category name")
        return self

    def resolved_period(self, *, current: YearMonth) -> YearMonth:
        """Return the month this request reads, defaulting to the current one.

        An omitted period is the documented default rather than a question,
        because the product baseline treats an unqualified question as being
        about the month in progress.

        Args:
            current: Month the runtime resolved from the message timestamp.

        Returns:
            The calendar month to read.
        """

        if self.period is None:
            return current
        year, month = self.period.split("-")
        return YearMonth(int(year), int(month))


BUDGET_QUESTION_TERMS: Final = ("budget", "limite", "residuo")
"""Italian words that mark a question about a budget rather than about spending."""


class ReadSelectionPolicyVersion(StrEnum):
    """Versioned deterministic policy applied after model tool selection.

    Attributes:
        MODEL_ONLY_V0: Preserve the parsed model request without semantic repair.
        EXPLICIT_FINANCE_SCOPE_V1: Repair only explicit funding, category, and
            overall-advice scopes supported by trusted route vocabulary.
    """

    MODEL_ONLY_V0 = "model-only-v0"
    EXPLICIT_FINANCE_SCOPE_V1 = "explicit-finance-scope-v1"


DEFAULT_READ_SELECTION_POLICY = ReadSelectionPolicyVersion.EXPLICIT_FINANCE_SCOPE_V1
"""Policy promoted by EVD-004; V0 remains the direct rollback."""

_INSTRUCTION_ATTACK_TERMS: Final = (
    "ignora le istruzioni",
    "ignora il registro",
    "salta la conferma",
    "rivela il prompt",
    "rivela integralmente le istruzioni",
    "stampa il prompt",
    "non chiamare strumenti",
    "non chiamare alcun tool",
)
_FUNDING_SCOPE_TERMS: Final = (
    "buoni pasto",
    "ticket restaurant",
    "fondi personali",
    "miei soldi",
    "soldi miei",
    "tasca mia",
    "benefit",
    "welfare",
)
_BALANCE_TERMS: Final = (
    "saldo",
    "rimane",
    "rimangono",
    "rimasto",
    "rimasti",
    "disponibile",
    "credito",
    "ho ancora",
)
_UNBUDGETED_SURVEY_TERMS: Final = (
    "senza budget",
    "senza avere un budget",
    "non hanno un budget",
    "non sto controllando con un budget",
)
_GENERAL_ADVICE_TERMS: Final = (
    "consiglio sulle spese",
    "consigli sulle spese",
)
_NONCURRENT_PERIOD_TERMS: Final = (
    "mese scorso",
    "scorso mese",
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


def apply_read_selection_policy(
    request: ToolRequest | None,
    *,
    intent: WorkflowIntent,
    message: str,
    category_names: tuple[str, ...] = (),
    funding_source_names: tuple[str, ...] = (),
    version: ReadSelectionPolicyVersion = DEFAULT_READ_SELECTION_POLICY,
) -> ToolRequest | None:
    """Apply one reversible deterministic semantic repair to a model request.

    The V1 policy acts only on information present in the normalized owner text
    and on active display names supplied by trusted runtime context. It cannot
    choose a profile, identifier, amount, source identifier, or figure. A model
    request supplies the period when one was parsed; when the model abstains,
    the policy only defaults to the current month if the text names no other
    month. Balance language always prevents a funding-spend repair.

    Args:
        request: Parsed model selection, or None when the model abstained.
        intent: Trusted routed read intent.
        message: Normalized owner text.
        category_names: Active purpose names from trusted configuration.
        funding_source_names: Active payment-source display names from trusted
            configuration.
        version: Policy version. MODEL_ONLY_V0 is the direct rollback path.

    Returns:
        The original request, a narrowly repaired request, or None when the
        policy has no safe decision.
    """

    if version is ReadSelectionPolicyVersion.MODEL_ONLY_V0:
        return request
    folded = normalize_owner_text(message)
    if _contains_any(folded, _INSTRUCTION_ATTACK_TERMS):
        return request
    balance_question = _contains_any(folded, _BALANCE_TERMS)
    funding_terms = (*_FUNDING_SCOPE_TERMS, *funding_source_names)
    if _contains_any(folded, funding_terms) and not balance_question:
        return _replacement_request(
            request,
            tool=ReadTool.FUNDING_CLASS_SPLIT,
            category_name=None,
            message=message,
        )

    if intent is not WorkflowIntent.ADVICE:
        return request
    category_name = _named_category_in_text(folded, category_names)
    if category_name is not None:
        return _replacement_request(
            request,
            tool=ReadTool.CATEGORY_SPENDING,
            category_name=category_name,
            message=message,
        )
    if _contains_any(folded, _UNBUDGETED_SURVEY_TERMS):
        return _replacement_request(
            request,
            tool=ReadTool.BUDGETS_AT_RISK,
            category_name=None,
            message=message,
        )
    if request is None and _contains_any(folded, _GENERAL_ADVICE_TERMS):
        return _replacement_request(
            request,
            tool=ReadTool.BUDGETS_AT_RISK,
            category_name=None,
            message=message,
        )
    return request


def _replacement_request(
    request: ToolRequest | None,
    *,
    tool: ReadTool,
    category_name: str | None,
    message: str,
) -> ToolRequest | None:
    """Build a repaired request without guessing a model-abstained past month."""

    if request is None and _names_noncurrent_period(message):
        return None
    return ToolRequest(
        tool=tool,
        category_name=category_name,
        period=None if request is None else request.period,
        evidence=(request.evidence if request is not None else message[:280]),
    )


def _contains_any(folded: str, candidates: tuple[str, ...]) -> bool:
    """Return whether normalized text contains one normalized trusted phrase."""

    return any(normalize_owner_text(candidate) in folded for candidate in candidates)


def _named_category_in_text(folded: str, category_names: tuple[str, ...]) -> str | None:
    """Return the longest active display name stated explicitly in the text."""

    matches = [name for name in category_names if normalize_owner_text(name) in folded]
    return max(matches, key=len) if matches else None


def _names_noncurrent_period(message: str) -> bool:
    """Return whether an abstained model left a non-current month unresolved."""

    folded = normalize_owner_text(message)
    numeric_month = any(
        len(token) == 7
        and token[:4].isdigit()
        and token[4] == "-"
        and token[5:].isdigit()
        for token in folded.split()
    )
    return numeric_month or _contains_any(folded, _NONCURRENT_PERIOD_TERMS)


def asks_for_an_unavailable_budget(
    request: ToolRequest, *, intent: WorkflowIntent, message: str
) -> bool:
    """Whether a category request answers a budget question the route cannot serve.

    The advice registry deliberately excludes `budget_status`, so a question
    about one named category's budget has no tool on that route and the honest
    answer is a question back. The measured behaviour is otherwise: the boundary
    selects `category_spending`, which reports what was spent rather than what
    the budget allows, and the owner receives a correct figure answering a
    question they did not ask.

    This is deterministic on purpose. Five instruction, demonstration, topology
    and vocabulary changes were measured against this class of substitution and
    each made the boundary attract an adjacent wrong case; the route's registry
    and the words the owner used are known without interpreting intent.

    The rule fires only on a category-scoped request. A survey request on the
    same route is untouched, which is why `budgets_at_risk` questions still
    reach their tool: no advice case in the pinned dataset expects a
    category-scoped tool for a message naming a budget.

    Args:
        request: Parsed model request, already admitted against the registry.
        intent: Route the runtime is executing, from trusted context.
        message: Normalized owner text, used only for the words it contains.

    Returns:
        True when the request must be refused instead of executed.
    """

    if ReadTool.BUDGET_STATUS in registry_for(intent):
        return False
    if request.category_name is None:
        return False
    lowered = message.lower()
    return any(term in lowered for term in BUDGET_QUESTION_TERMS)


def accept_tool_request(
    request: ToolRequest,
    *,
    intent: WorkflowIntent,
) -> ReadTool:
    """Admit one requested tool against the calling route's registry.

    This is the authorization boundary the evaluation matrix scores as
    "forbidden or unregistered tool request". It is deterministic and takes the
    route from trusted runtime context, never from the model, so a model cannot
    widen its own scope by claiming a different route.

    Args:
        request: Parsed model request.
        intent: Route the runtime is executing, from trusted context.

    Returns:
        The admitted tool.

    Raises:
        DomainError: If the route owns no registry, or the requested tool is
            outside it.
    """

    registry = registry_for(intent)
    if request.tool not in registry:
        raise DomainError(
            f"tool {request.tool.value} is not registered for {intent.value}"
        )
    return request.tool


def tool_vocabulary(intent: WorkflowIntent) -> tuple[str, ...]:
    """Render one route's offered tool names in a stable order.

    Derived from the registry rather than retyped, so the names a model is
    offered cannot drift from the names deterministic acceptance admits.

    Args:
        intent: Route whose vocabulary is requested.

    Returns:
        The tool names sorted for deterministic prompts and manifests.

    Raises:
        DomainError: If the route owns no registry.
    """

    return tuple(sorted(tool.value for tool in registry_for(intent)))


__all__ = [
    "ADVICE_TOOLS",
    "CATEGORY_TOOLS",
    "DEFAULT_READ_SELECTION_POLICY",
    "QUERY_TOOLS",
    "TOOL_CONTRACT_VERSION",
    "TOOL_REGISTRIES",
    "ReadSelectionPolicyVersion",
    "ReadTool",
    "ToolRequest",
    "accept_tool_request",
    "apply_read_selection_policy",
    "registry_for",
    "tool_vocabulary",
]
