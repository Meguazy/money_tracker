"""Strict HTTP contracts for read-only, tool-grounded advice."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from money_tracker.api.contracts import MoneyContract
from money_tracker.domain import FundingClass, YearMonth
from money_tracker.web.advice import AdviceRun
from money_tracker.web.contracts import (
    BudgetPositionContract,
    FundingClassSplitContract,
    FundingSourceSpendContract,
    PeriodContract,
)
from money_tracker.workflows import ReadOutcome, ReadReason, ReadTool


class _AdviceContract(BaseModel):
    """Reject unknown or coercion-dependent fields at the advice boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AdviceRequest(_AdviceContract):
    """One bounded owner request for the read-only advice route."""

    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def _normalize_message(cls, message: str) -> str:
        """Collapse whitespace and refuse content-free requests."""

        normalized = " ".join(message.split())
        if not normalized:
            raise ValueError("advice message cannot be blank")
        return normalized


class AdviceCategorySpendingEvidence(_AdviceContract):
    """Posted category spend with no invented budget or historical threshold."""

    kind: Literal[ReadTool.CATEGORY_SPENDING] = ReadTool.CATEGORY_SPENDING
    period: PeriodContract
    category_name: str
    posted_spend: MoneyContract
    funding_split: FundingClassSplitContract
    assessment_status: Literal["spending_without_threshold"] = (
        "spending_without_threshold"
    )


class AdviceFundingSplitEvidence(_AdviceContract):
    """Posted payment consumption, explicitly distinct from wallet balance."""

    kind: Literal[ReadTool.FUNDING_CLASS_SPLIT] = ReadTool.FUNDING_CLASS_SPLIT
    period: PeriodContract
    funding_split: FundingClassSplitContract
    funding_sources: tuple[FundingSourceSpendContract, ...]
    assessment_status: Literal["consumption_not_balance"] = "consumption_not_balance"


class AdviceBudgetRiskEvidence(_AdviceContract):
    """Budget positions and categories whose posted spend has no budget."""

    kind: Literal[ReadTool.BUDGETS_AT_RISK] = ReadTool.BUDGETS_AT_RISK
    period: PeriodContract
    positions: tuple[BudgetPositionContract, ...]
    unbudgeted_categories: tuple[str, ...]
    assessment_status: Literal["budget_evidence", "insufficient_data"]


AdviceEvidence = Annotated[
    AdviceCategorySpendingEvidence
    | AdviceFundingSplitEvidence
    | AdviceBudgetRiskEvidence,
    Field(discriminator="kind"),
]


class AdviceResponse(_AdviceContract):
    """Safe advice outcome containing only deterministic numeric evidence."""

    status: Literal["answered", "needs_clarification"]
    evidence: AdviceEvidence | None
    reason: (
        Literal[
            "category_not_found", "capability_not_available", "request_not_understood"
        ]
        | None
    )

    @model_validator(mode="after")
    def _validate_result_shape(self) -> Self:
        """Require evidence only for answers and a reason only for refusals."""

        if self.status == "answered" and (
            self.evidence is None or self.reason is not None
        ):
            raise ValueError("an answered advice response requires only evidence")
        if self.status == "needs_clarification" and (
            self.evidence is not None or self.reason is None
        ):
            raise ValueError("a clarification requires only a public reason")
        return self

    @classmethod
    def from_workflow(cls, result: AdviceRun) -> Self:
        """Map the promoted workflow result without exposing model output."""

        if result.outcome is ReadOutcome.CLARIFICATION:
            return cls(
                status="needs_clarification",
                evidence=None,
                reason=_public_reason(result.clarification_reason),
            )
        if result.executed_tool is None or result.facts is None:
            raise ValueError("answered advice must carry deterministic tool facts")
        return cls(
            status="answered",
            evidence=_evidence(result.executed_tool, result.facts),
            reason=None,
        )


def _public_reason(
    reason: ReadReason | None,
) -> Literal[
    "category_not_found", "capability_not_available", "request_not_understood"
]:
    """Reduce internal workflow reasons to bounded owner-facing states."""

    if reason is ReadReason.UNKNOWN_CATEGORY:
        return "category_not_found"
    if reason in {
        ReadReason.TOOL_NOT_REGISTERED,
        ReadReason.BUDGET_TOOL_UNAVAILABLE,
    }:
        return "capability_not_available"
    return "request_not_understood"


def _evidence(tool: ReadTool, facts: dict[str, Any]) -> AdviceEvidence:
    """Validate deterministic tool JSON into the strict browser union."""

    period = _period(facts["period"])
    if tool is ReadTool.CATEGORY_SPENDING:
        return AdviceCategorySpendingEvidence(
            period=period,
            category_name=cast(str, facts["category_name"]),
            posted_spend=_money(facts["posted_spend"]),
            funding_split=_split(facts["split"]),
        )
    if tool is ReadTool.FUNDING_CLASS_SPLIT:
        sources = cast(list[dict[str, Any]], facts["sources"])
        return AdviceFundingSplitEvidence(
            period=period,
            funding_split=_split(facts["split"]),
            funding_sources=tuple(
                FundingSourceSpendContract(
                    name=cast(str, source["name"]),
                    funding_class=FundingClass(cast(str, source["funding_class"])),
                    amount=_money(source["posted_spend"]),
                )
                for source in sources
            ),
        )
    if tool is not ReadTool.BUDGETS_AT_RISK:
        raise ValueError("tool is not available to the promoted advice registry")
    statuses = cast(list[dict[str, Any]], facts["statuses"])
    unbudgeted = tuple(cast(list[str], facts["unbudgeted_category_names"]))
    positions = tuple(
        BudgetPositionContract(
            category_name=cast(str, status["category_name"]),
            target=_money(status["target"]),
            posted_spend=_money(status["posted_spend"]),
            remaining=_money(status["remaining"]),
        )
        for status in statuses
    )
    return AdviceBudgetRiskEvidence(
        period=period,
        positions=positions,
        unbudgeted_categories=unbudgeted,
        assessment_status=(
            "budget_evidence" if positions or unbudgeted else "insufficient_data"
        ),
    )


def _period(value: Any) -> PeriodContract:
    """Validate the monthly period returned by a promoted read tool."""

    if not isinstance(value, str):
        raise ValueError("advice period is not text")
    year, separator, month = value.partition("-")
    if separator != "-" or not year.isdigit() or not month.isdigit():
        raise ValueError("advice period is not YYYY-MM")
    return PeriodContract.from_domain(YearMonth(int(year), int(month)))


def _money(value: Any) -> MoneyContract:
    """Validate exact EUR minor units returned by a promoted read tool."""

    return MoneyContract.model_validate(value)


def _split(value: Any) -> FundingClassSplitContract:
    """Validate the three exact funding partitions returned by a read tool."""

    if not isinstance(value, dict):
        raise ValueError("advice funding split is not an object")
    return FundingClassSplitContract(
        personal_funds=_money(value["personal_funds"]),
        benefit=_money(value["benefit"]),
        other=_money(value["other"]),
    )


__all__ = ["AdviceRequest", "AdviceResponse"]
