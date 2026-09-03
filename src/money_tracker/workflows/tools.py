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
from typing import Annotated, Self

from pydantic import StringConstraints, model_validator

from money_tracker.domain.budgets import YearMonth
from money_tracker.domain.errors import DomainError
from money_tracker.workflows.contracts import (
    EvidenceText,
    NonEmptyText,
    _WorkflowContract,
)
from money_tracker.workflows.intents import WorkflowIntent

TOOL_CONTRACT_VERSION = "v1"
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
    "QUERY_TOOLS",
    "TOOL_CONTRACT_VERSION",
    "TOOL_REGISTRIES",
    "ReadTool",
    "ToolRequest",
    "accept_tool_request",
    "registry_for",
    "tool_vocabulary",
]
