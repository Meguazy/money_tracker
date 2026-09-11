"""Model-facing boundary schemas shaped for reliable local decoding.

The contracts in `contracts` say what a boundary means. These say how a local
model is asked for it, and the two differ for one measured reason.

The Step 1 capability screen at suite version 1.2.0 established two things
about the screened text candidate. It cannot reliably leave an optional field
alone: across 68 slots a message left silent it emptied 0.7647 of them under
the required-and-nullable shape and 0.4265 under the genuinely-optional shape,
against a 0.95 bar. And it answers a discriminated union cleanly: every answer
matched exactly one variant and none carried a field belonging to the variant
it did not choose, because the decoding grammar cannot emit one.

Every field a message may leave unstated is therefore a union here, with "not
stated" as an explicit variant carrying no payload at all. Filling a field the
message never mentioned stops being a judgment the model has to get right and
becomes a shape it cannot express. The same move carries the one cross-field
rule that a validator used to enforce after the fact: a single named payment
method and a split are separate variants, so an unquantified split is
unexpressible rather than rejected.

Conversion to the domain contract is pure, total, and the only thing these
types do. They own no policy: what an absent value means is decided by the
deterministic resolvers exactly as before.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, Union

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from money_tracker.domain import YearMonth
from money_tracker.workflows.contracts import (
    CategoryFit,
    CategoryFitOutcome,
    EvidenceText,
    ExpenseExtraction,
    ExpensePolarity,
    ExtractedPaymentPart,
    NonEmptyText,
)
from money_tracker.workflows.tools import ReadTool, ToolRequest


class _WireContract(BaseModel):
    """Reject unknown and coercion-dependent fields at the model boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AmountStated(_WireContract):
    """The message states a total magnitude.

    Attributes:
        kind: Variant discriminator.
        minor_units: Positive total magnitude in euro cents.
    """

    kind: Literal["amount_stated"]
    minor_units: int = Field(gt=0)


class AmountNotStated(_WireContract):
    """The message states no figure at all.

    Attributes:
        kind: Variant discriminator.
    """

    kind: Literal["amount_not_stated"]


WireAmount = Annotated[AmountStated | AmountNotStated, Field(discriminator="kind")]
"""Total magnitude, or an explicit statement that the message gives none."""


class TimeStated(_WireContract):
    """The message states when the movement happened.

    Attributes:
        kind: Variant discriminator.
        text: The owner's own temporal words, copied verbatim.
    """

    kind: Literal["time_stated"]
    text: NonEmptyText


class TimeNotStated(_WireContract):
    """The message states no time, so the movement just happened.

    Attributes:
        kind: Variant discriminator.
    """

    kind: Literal["time_not_stated"]


WireOccurrence = Annotated[TimeStated | TimeNotStated, Field(discriminator="kind")]
"""Occurrence words, or an explicit statement that the message gives none."""


class MerchantNamed(_WireContract):
    """The message names a shop, brand, or business.

    Attributes:
        kind: Variant discriminator.
        name: The merchant as the message names it.
    """

    kind: Literal["merchant_named"]
    name: NonEmptyText


class MerchantNotNamed(_WireContract):
    """The message names no merchant.

    Attributes:
        kind: Variant discriminator.
    """

    kind: Literal["merchant_not_named"]


WireMerchant = Annotated[MerchantNamed | MerchantNotNamed, Field(discriminator="kind")]
"""Merchant, or an explicit statement that the message names none."""


class SplitPart(_WireContract):
    """One quantified portion of a payment split across methods.

    Attributes:
        funding_source_text: The owner's own words for the method.
        minor_units: This portion's magnitude in euro cents.
    """

    funding_source_text: NonEmptyText
    minor_units: int = Field(gt=0)


class FundingOneMethod(_WireContract):
    """The message names one payment method covering the whole total.

    The method carries no amount of its own, because it covers everything. That
    is a property of this variant rather than a rule a validator has to check.

    Attributes:
        kind: Variant discriminator.
        funding_source_text: The owner's own words for the method.
    """

    kind: Literal["funding_one_method"]
    funding_source_text: NonEmptyText


class FundingSplit(_WireContract):
    """The message splits the payment across two or more methods.

    Every portion is quantified, because the variant has no shape in which one
    is missing. An unquantified split could never reconcile, and it used to be
    rejected by a validator after the model had already produced it.

    Attributes:
        kind: Variant discriminator.
        parts: Two or more quantified portions.
    """

    kind: Literal["funding_split"]
    parts: tuple[SplitPart, ...] = Field(min_length=2)


class FundingNotStated(_WireContract):
    """The message names no payment method, so the profile default applies.

    Attributes:
        kind: Variant discriminator.
    """

    kind: Literal["funding_not_stated"]


WireFunding = Annotated[
    FundingOneMethod | FundingSplit | FundingNotStated,
    Field(discriminator="kind"),
]
"""Funding, as one method, a quantified split, or an explicit absence."""


class WireExpenseExtraction(_WireContract):
    """One expense or refund candidate as a local model is asked for it.

    Every field the owner may leave unmentioned is a union whose "not stated"
    variant carries no payload, so inventing a merchant, a time, a method, or a
    figure is not a mistake the model can make in this shape.

    Attributes:
        polarity: Whether money left or returned to the owner.
        amount: Total magnitude, or an explicit absence.
        occurrence: Temporal words, or an explicit absence.
        description: Owner-facing description of the movement.
        merchant: Merchant, or an explicit absence.
        funding: One method, a quantified split, or an explicit absence.
        evidence: Short verbatim span supporting the extraction.
    """

    polarity: ExpensePolarity
    amount: WireAmount
    occurrence: WireOccurrence
    description: NonEmptyText
    merchant: WireMerchant
    funding: WireFunding
    evidence: EvidenceText

    @model_validator(mode="after")
    def _require_distinct_split_methods(self) -> Self:
        """Keep a split's methods distinct, the one rule the shape can carry.

        Whether a split adds up is deliberately not checked here. It was, for
        one run, and that was a mistake worth recording: a split whose parts do
        not match the stated total is often the owner's own arithmetic, and the
        product answer is to tell them so. Refusing it at the boundary made the
        model adjust one of the owner's numbers until it reconciled, which
        turned two cases the dataset expects to surface as
        `split_does_not_reconcile` into silently resolved drafts. An invariant
        the deterministic side must report cannot be enforced where the model
        can satisfy it by changing the evidence.

        Returns:
            The validated extraction.

        Raises:
            ValueError: If a split names the same method twice, which no
                allocation could reconcile whatever the amounts are.
        """

        if isinstance(self.funding, FundingSplit):
            named = [
                " ".join(part.funding_source_text.split()).casefold()
                for part in self.funding.parts
            ]
            if len(set(named)) != len(named):
                raise ValueError("a split must name distinct payment methods")
        return self

    def to_domain(self) -> ExpenseExtraction:
        """Convert to the contract the deterministic resolvers consume.

        Returns:
            The equivalent `ExpenseExtraction`, in which an unstated field is
            the absence the resolvers already interpret.
        """

        parts: tuple[ExtractedPaymentPart, ...] = ()
        if isinstance(self.funding, FundingOneMethod):
            parts = (
                ExtractedPaymentPart(
                    funding_source_text=self.funding.funding_source_text,
                    amount_minor_units=None,
                ),
            )
        elif isinstance(self.funding, FundingSplit):
            parts = tuple(
                ExtractedPaymentPart(
                    funding_source_text=part.funding_source_text,
                    amount_minor_units=part.minor_units,
                )
                for part in self.funding.parts
            )
        return ExpenseExtraction(
            polarity=self.polarity,
            amount_minor_units=(
                self.amount.minor_units
                if isinstance(self.amount, AmountStated)
                else None
            ),
            occurred_at_text=(
                self.occurrence.text
                if isinstance(self.occurrence, TimeStated)
                else None
            ),
            description=self.description,
            merchant=(
                self.merchant.name if isinstance(self.merchant, MerchantNamed) else None
            ),
            payment_parts=parts,
            evidence=self.evidence,
        )


class ExistingCategoryChoice(_WireContract):
    """An active category already describes the movement's purpose.

    Attributes:
        kind: Variant discriminator.
        category_name: Active category name or alias, exactly as listed.
        evidence: Short verbatim span of the message supporting the judgment.
    """

    kind: Literal["existing_category"]
    category_name: NonEmptyText
    evidence: EvidenceText


class NewCategoryProposal(_WireContract):
    """No active category is adequate, so one is offered for owner approval.

    The branch requires the closest active category and why it does not fit.
    That is the comparison the flat schema let the model skip: it proposed on
    131 of 141 cases where an adequate node existed, while naming the correct
    existing category in half of those same proposals. Making the comparison a
    field of the proposal turns it from a step the model may omit into one the
    shape will not let it omit.

    Attributes:
        kind: Variant discriminator.
        closest_existing_category: The active category that comes nearest,
            named exactly as listed.
        why_closest_is_inadequate: Why that category does not describe this
            purpose.
        proposed_name: Candidate display name for the new category.
        parent_name: Existing top-level category the candidate belongs under.
        description: Inclusion guidance for the candidate.
        evidence: Short verbatim span of the message supporting the judgment.
    """

    kind: Literal["new_category"]
    closest_existing_category: NonEmptyText
    why_closest_is_inadequate: NonEmptyText
    proposed_name: NonEmptyText
    parent_name: NonEmptyText
    description: NonEmptyText
    evidence: EvidenceText


WireCategoryJudgment = Annotated[
    ExistingCategoryChoice | NewCategoryProposal,
    Field(discriminator="kind"),
]
"""Either an existing category or a proposal, never fields of both."""


class WireCategoryFit(_WireContract):
    """One purpose judgment as a local model is asked for it.

    The flat `CategoryFit` carries both halves and lets an enum decide which
    one is meaningful, which is how the model came to fill `proposed_name`
    beside `category_name`. Here the halves are separate variants, so an answer
    carrying both is not a mistake the decoding grammar can produce.

    Attributes:
        judgment: The selected variant.
    """

    judgment: WireCategoryJudgment

    def to_domain(self) -> CategoryFit:
        """Convert to the contract the deterministic resolution consumes.

        Returns:
            The equivalent `CategoryFit`, carrying only the half its outcome
            selects.
        """

        if isinstance(self.judgment, ExistingCategoryChoice):
            return CategoryFit(
                outcome=CategoryFitOutcome.EXISTING,
                category_name=self.judgment.category_name,
                evidence=self.judgment.evidence,
            )
        return CategoryFit(
            outcome=CategoryFitOutcome.PROPOSAL,
            proposed_name=self.judgment.proposed_name,
            parent_name=self.judgment.parent_name,
            description=self.judgment.description,
            rationale=self.judgment.why_closest_is_inadequate,
            considered_category_names=(self.judgment.closest_existing_category,),
            evidence=self.judgment.evidence,
        )


class PeriodCurrentMonth(_WireContract):
    """The message names no month, so the month in progress is meant.

    Attributes:
        kind: Variant discriminator.
    """

    kind: Literal["current_month"]


class PeriodPreviousMonth(_WireContract):
    """The message names the month before the one in progress.

    This variant exists because the flat boundary could not express it. It
    asked for a month as `YYYY-MM` and never told the model which month is
    current, so `il mese scorso` had no answer but a guessed year.

    Attributes:
        kind: Variant discriminator.
    """

    kind: Literal["previous_month"]


class PeriodMonthThisYear(_WireContract):
    """The message names a month but no year.

    A bare month name is the most recent one that has already occurred, which
    the assistant resolves from the message timestamp. The flat boundary had no
    shape for this and the union's first version had none either, so `ad
    agosto` forced the model to supply a year: it answered 2023 against a
    fixture standing in 2026, and the system read an empty month.

    Attributes:
        kind: Variant discriminator.
        month: Calendar month the message names, 1 through 12.
    """

    kind: Literal["month_this_year"]
    month: int = Field(ge=1, le=12)


class PeriodNamedMonth(_WireContract):
    """The message names one calendar month outright.

    Attributes:
        kind: Variant discriminator.
        year: Four-digit calendar year the message names.
        month: Calendar month the message names, 1 through 12.
    """

    kind: Literal["named_month"]
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


WirePeriod = Annotated[
    PeriodCurrentMonth | PeriodPreviousMonth | PeriodMonthThisYear | PeriodNamedMonth,
    Field(discriminator="kind"),
]
"""The month a tool reads, as a choice rather than as a string to fill."""


class CategoryToolRequest(_WireContract):
    """A read tool that rolls up one named category.

    Attributes:
        kind: Variant discriminator.
        tool: The requested category tool.
        category_name: Category as the owner named it.
        period: The month to read.
        evidence: Short verbatim span of the message supporting the choice.
    """

    kind: Literal["category_tool"]
    tool: Literal[ReadTool.CATEGORY_SPENDING, ReadTool.BUDGET_STATUS]
    category_name: NonEmptyText
    period: WirePeriod
    evidence: EvidenceText


class OverallToolRequest(_WireContract):
    """A read tool that rolls up no single category.

    Attributes:
        kind: Variant discriminator.
        tool: The requested overall tool.
        period: The month to read.
        evidence: Short verbatim span of the message supporting the choice.
    """

    kind: Literal["overall_tool"]
    tool: Literal[ReadTool.FUNDING_CLASS_SPLIT, ReadTool.BUDGETS_AT_RISK]
    period: WirePeriod
    evidence: EvidenceText


class NoAnswerableTool(_WireContract):
    """No tool offered to this route answers the owner's question.

    The boundary had no way to say this. It offered only tools, so a question
    none of them answers still had to be answered with one, and six adversarial
    cases that exist to be refused were answered instead from whichever tool
    was nearest. Declining is now a shape, and the graph turns it into the same
    clarification it already produces for a tool outside the route's registry.

    Attributes:
        kind: Variant discriminator.
        evidence: Short verbatim span of the message the tools cannot answer.
    """

    kind: Literal["no_answerable_tool"]
    evidence: EvidenceText


WireToolSelection = Annotated[
    CategoryToolRequest | OverallToolRequest | NoAnswerableTool,
    Field(discriminator="kind"),
]
"""A category tool, an overall tool, or an explicit refusal to choose one."""


class WireToolRequest(_WireContract):
    """One read-tool selection as a local model is asked for it.

    Two conditional rules the flat boundary enforced after the fact are shapes
    here instead. A category tool cannot arrive without its category and an
    overall tool cannot arrive with one, because neither has a field for the
    mistake. And a month is chosen rather than written, which is what stops an
    unstated period from being invented: the flat boundary supplied one on 79%
    of the messages that named none, and the system then read that month, found
    nothing, and answered zero.

    Attributes:
        request: The selected variant.
    """

    request: WireToolSelection

    def to_domain(self, *, current: YearMonth) -> ToolRequest | None:
        """Convert to the contract deterministic acceptance validates.

        Args:
            current: Month the runtime resolved from the message timestamp,
                which the model is never shown.

        Returns:
            The equivalent `ToolRequest`, whose absent period keeps meaning the
            month in progress, or None when the boundary declined to choose.
        """

        if isinstance(self.request, NoAnswerableTool):
            return None
        period = self.request.period
        resolved: str | None = None
        if isinstance(period, PeriodPreviousMonth):
            year, month = (
                (current.year, current.month - 1)
                if current.month > 1
                else (current.year - 1, 12)
            )
            resolved = f"{year:04d}-{month:02d}"
        elif isinstance(period, PeriodMonthThisYear):
            year = current.year if period.month <= current.month else current.year - 1
            resolved = f"{year:04d}-{period.month:02d}"
        elif isinstance(period, PeriodNamedMonth):
            resolved = f"{period.year:04d}-{period.month:02d}"
        return ToolRequest(
            tool=self.request.tool,
            category_name=(
                self.request.category_name
                if isinstance(self.request, CategoryToolRequest)
                else None
            ),
            period=resolved,
            evidence=self.request.evidence,
        )


def build_category_fit_schema(
    active_names: tuple[str, ...],
    root_names: tuple[str, ...] = (),
) -> type[BaseModel]:
    """Build a category judgment whose names are a closed set.

    `WireCategoryFit` asks for a category as free text and resolves it against
    the taxonomy afterwards, which leaves the model free to produce a name that
    does not exist. It does: asked to copy `Cura personale` character for
    character it answered `Curare personale` and `Curia personale`, and three
    correct judgments were discarded as unresolvable. Instructing it more
    firmly did not help, because this is decoding rather than judgment.

    Binding the names into the schema makes the mistake unexpressible. The
    decoding grammar can then emit only a name the owner actually has, which is
    the same move the union made for the branch itself.

    The schema is built per taxonomy rather than declared once, because the
    owner's categories are data. Resolution stays deterministic and unchanged:
    the boundary still names a category and never identifies one.

    The same reasoning covers the parent a proposal asks for, and it did not
    until an owner met it. The domain admits only an active root there, the
    schema admitted any string, and the model reasonably answered with the
    nearest category, which is usually a leaf. Every such proposal was refused
    with `category_parent_invalid`, and the owner had no way forward. Approving
    a category made it worse, because the new leaf then became the most
    attractive parent for everything related to it.

    Args:
        active_names: Active category display names, in taxonomy order.
        root_names: Active root display names a proposal may sit under.
            Defaults to `active_names` only for callers that predate the
            distinction, which leaves the old unconstrained behaviour visible
            rather than silently narrowing it.

    Returns:
        A contract equivalent to `WireCategoryFit` whose category names are
        constrained to `active_names` and whose parent is constrained to
        `root_names`.

    Raises:
        ValueError: If no active category was supplied.
    """

    if not active_names:
        raise ValueError("category fit requires at least one active category")
    name_type: Any = Literal[active_names]
    parent_type: Any = Literal[root_names or active_names]
    existing = create_model(
        "BoundExistingCategoryChoice",
        __config__=ConfigDict(extra="forbid", strict=True),
        kind=(Literal["existing_category"], ...),
        category_name=(name_type, ...),
        evidence=(EvidenceText, ...),
    )
    proposal = create_model(
        "BoundNewCategoryProposal",
        __config__=ConfigDict(extra="forbid", strict=True),
        kind=(Literal["new_category"], ...),
        closest_existing_category=(name_type, ...),
        why_closest_is_inadequate=(NonEmptyText, ...),
        proposed_name=(NonEmptyText, ...),
        parent_name=(parent_type, ...),
        description=(NonEmptyText, ...),
        evidence=(EvidenceText, ...),
    )
    judgment: Any = Annotated[
        Union[existing, proposal],  # noqa: UP007 - built at runtime, not a syntax union
        Field(discriminator="kind"),
    ]
    return create_model(
        "BoundWireCategoryFit",
        __config__=ConfigDict(extra="forbid", strict=True),
        __doc__=WireCategoryFit.__doc__,
        judgment=(judgment, ...),
    )


def build_subtree_choice_schema(heading_names: tuple[str, ...]) -> type[BaseModel]:
    """Build the first stage of a two-stage category judgment.

    The stage answers which heading covers the purpose, and offers no child, so
    a heading answer here carries no information about the level. The proposal
    lives in this stage's second variant, because a purpose no heading covers is
    exactly what a proposal is, and because the parent a proposal needs is a
    heading the model is already choosing between.

    Args:
        heading_names: Active root category names, in taxonomy order.

    Returns:
        A discriminated union over `subtree` and `no_heading_fits`.

    Raises:
        ValueError: If no heading was supplied.
    """

    if not heading_names:
        raise ValueError("subtree choice requires at least one heading")
    name_type: Any = Literal[heading_names]
    chosen = create_model(
        "BoundSubtreeChoice",
        __config__=ConfigDict(extra="forbid", strict=True),
        kind=(Literal["subtree"], ...),
        heading_name=(name_type, ...),
        evidence=(EvidenceText, ...),
    )
    proposal = create_model(
        "BoundNoHeadingFits",
        __config__=ConfigDict(extra="forbid", strict=True),
        kind=(Literal["no_heading_fits"], ...),
        closest_heading=(name_type, ...),
        why_closest_is_inadequate=(NonEmptyText, ...),
        proposed_name=(NonEmptyText, ...),
        parent_name=(name_type, ...),
        description=(NonEmptyText, ...),
        evidence=(EvidenceText, ...),
    )
    choice: Any = Annotated[
        Union[chosen, proposal],  # noqa: UP007 - built at runtime, not a syntax union
        Field(discriminator="kind"),
    ]
    return create_model(
        "BoundWireSubtreeChoice",
        __config__=ConfigDict(extra="forbid", strict=True),
        choice=(choice, ...),
    )


def build_leaf_choice_schema(
    heading_name: str, child_names: tuple[str, ...]
) -> type[BaseModel]:
    """Build the second stage of a two-stage category judgment.

    The stage answers the level within one heading. The heading competes only
    against its own children, which is the comparison the taxonomy's shape makes
    meaningful, and the enum is three or four values rather than eighteen. A
    heading with no children never reaches this stage.

    Args:
        heading_name: Heading the first stage chose.
        child_names: That heading's active children, in taxonomy order.

    Returns:
        A discriminated union over `child` and `the_heading`.

    Raises:
        ValueError: If the heading has no children.
    """

    if not child_names:
        raise ValueError(f"{heading_name} has no children to choose between")
    name_type: Any = Literal[child_names]
    child = create_model(
        "BoundChildChoice",
        __config__=ConfigDict(extra="forbid", strict=True),
        kind=(Literal["child"], ...),
        category_name=(name_type, ...),
        evidence=(EvidenceText, ...),
    )
    heading = create_model(
        "BoundHeadingChoice",
        __config__=ConfigDict(extra="forbid", strict=True),
        kind=(Literal["the_heading"], ...),
        why_no_child_fits=(NonEmptyText, ...),
        evidence=(EvidenceText, ...),
    )
    choice: Any = Annotated[
        Union[child, heading],  # noqa: UP007 - built at runtime, not a syntax union
        Field(discriminator="kind"),
    ]
    return create_model(
        "BoundWireLeafChoice",
        __config__=ConfigDict(extra="forbid", strict=True),
        choice=(choice, ...),
    )


def subtree_choice_to_domain(parsed: BaseModel, heading: str) -> CategoryFit:
    """Convert a first-stage answer that ends the judgment.

    A `subtree` answer does not end it: the level is still open, so the caller
    runs the second stage. Only `no_heading_fits` is terminal here.

    Args:
        parsed: A schema from `build_subtree_choice_schema`.
        heading: The heading a `subtree` answer chose, used when the caller has
            already decided the heading is childless and therefore final.

    Returns:
        The equivalent `CategoryFit`.
    """

    choice = parsed.choice  # type: ignore[attr-defined]
    if choice.kind == "subtree":
        return CategoryFit(
            outcome=CategoryFitOutcome.EXISTING,
            category_name=heading,
            evidence=choice.evidence,
        )
    return CategoryFit(
        outcome=CategoryFitOutcome.PROPOSAL,
        proposed_name=choice.proposed_name,
        parent_name=choice.parent_name,
        description=choice.description,
        rationale=choice.why_closest_is_inadequate,
        considered_category_names=(choice.closest_heading,),
        evidence=choice.evidence,
    )


def leaf_choice_to_domain(parsed: BaseModel, heading: str) -> CategoryFit:
    """Convert a second-stage answer into the domain judgment.

    Args:
        parsed: A schema from `build_leaf_choice_schema`.
        heading: The heading the first stage chose, which is the answer when no
            child fits.

    Returns:
        The equivalent `CategoryFit`, always an existing category.
    """

    choice = parsed.choice  # type: ignore[attr-defined]
    name = heading if choice.kind == "the_heading" else choice.category_name
    return CategoryFit(
        outcome=CategoryFitOutcome.EXISTING,
        category_name=name,
        evidence=choice.evidence,
    )


def category_fit_to_domain(parsed: BaseModel) -> CategoryFit:
    """Convert either category-fit contract to the domain judgment.

    Args:
        parsed: A `WireCategoryFit` or a schema from
            `build_category_fit_schema`, which carry the same fields.

    Returns:
        The equivalent `CategoryFit`.
    """

    judgment = parsed.judgment  # type: ignore[attr-defined]
    if judgment.kind == "existing_category":
        return CategoryFit(
            outcome=CategoryFitOutcome.EXISTING,
            category_name=judgment.category_name,
            evidence=judgment.evidence,
        )
    return CategoryFit(
        outcome=CategoryFitOutcome.PROPOSAL,
        proposed_name=judgment.proposed_name,
        parent_name=judgment.parent_name,
        description=judgment.description,
        rationale=judgment.why_closest_is_inadequate,
        considered_category_names=(judgment.closest_existing_category,),
        evidence=judgment.evidence,
    )


__all__ = [
    "AmountNotStated",
    "build_category_fit_schema",
    "category_fit_to_domain",
    "CategoryToolRequest",
    "NoAnswerableTool",
    "OverallToolRequest",
    "PeriodCurrentMonth",
    "PeriodMonthThisYear",
    "PeriodNamedMonth",
    "PeriodPreviousMonth",
    "WirePeriod",
    "WireToolRequest",
    "WireToolSelection",
    "ExistingCategoryChoice",
    "NewCategoryProposal",
    "WireCategoryFit",
    "WireCategoryJudgment",
    "AmountStated",
    "FundingNotStated",
    "FundingOneMethod",
    "FundingSplit",
    "MerchantNamed",
    "MerchantNotNamed",
    "SplitPart",
    "TimeNotStated",
    "TimeStated",
    "WireAmount",
    "WireExpenseExtraction",
    "WireFunding",
    "WireMerchant",
    "WireOccurrence",
]
