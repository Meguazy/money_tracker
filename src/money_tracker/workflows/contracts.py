"""Versioned strict schemas for the router's model boundary.

Every value in this module is produced by a local model and is therefore
untrusted input. The schemas deliberately expose no profile, timezone, funding
source, authorization, or idempotency field: AD-006 reserves those for trusted
runtime context, so the model has no shape in which to supply one.

Deterministic validation of a parsed classification lives in
`money_tracker.workflows.routing`; this module only fixes the wire shape.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from money_tracker.workflows.intents import (
    DETERMINISTIC_ONLY_INTENTS,
    WorkflowIntent,
)

ROUTER_CONTRACT_VERSION = "v1"
"""Version of the router model boundary.

Interrupted workflow threads are resumed against the version that created
them, so a breaking change to any schema here requires a new version and a
documented migration rather than an edit in place.
"""

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

EvidenceText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=280),
]


class _WorkflowContract(BaseModel):
    """Reject unknown and coercion-dependent fields at the model boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RouteClassification(_WorkflowContract):
    """One model-produced routing decision awaiting deterministic validation.

    Attributes:
        intent: Requested destination, never a deterministic-only route.
        confidence: Self-reported certainty, treated as a diagnostic signal
            that deterministic policy may override but never trust alone.
        evidence: Short verbatim span of the owner message that supports the
            decision, so a reviewer can audit the choice without the prompt.
        unknowns: Aspects the model could not resolve, stated explicitly rather
            than guessed.
    """

    intent: WorkflowIntent
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: EvidenceText
    unknowns: tuple[NonEmptyText, ...] = ()

    @field_validator("intent")
    @classmethod
    def _reject_deterministic_only_intent(
        cls, intent: WorkflowIntent
    ) -> WorkflowIntent:
        """Refuse a route that deterministic pre-routing alone may assign.

        Args:
            intent: Intent requested by the model.

        Returns:
            The validated intent.

        Raises:
            ValueError: If the model claimed a deterministic-only route.
        """

        if intent in DETERMINISTIC_ONLY_INTENTS:
            raise ValueError(f"intent {intent.value} is assigned deterministically")
        return intent

    @field_validator("unknowns")
    @classmethod
    def _reject_repeated_unknowns(cls, unknowns: tuple[str, ...]) -> tuple[str, ...]:
        """Keep the unresolved-aspect list a set of distinct statements.

        Args:
            unknowns: Declared unresolved aspects.

        Returns:
            The validated unknowns.

        Raises:
            ValueError: If the same unknown was declared more than once.
        """

        if len(set(unknowns)) != len(unknowns):
            raise ValueError("unknowns must be distinct")
        return unknowns


EXTRACTION_CONTRACT_VERSION = "v1"
"""Version of the expense and refund extraction model boundary.

Versioned independently of the router boundary so that one can change without
invalidating interrupted threads that only depend on the other.
"""


class ExpensePolarity(StrEnum):
    """Direction of an extracted movement.

    The model states direction as a word and the magnitude as a positive
    integer. Deterministic resolution applies the sign the domain expects, so a
    misplaced minus sign cannot turn a purchase into a reimbursement.

    Attributes:
        EXPENSE: Money left the owner.
        REFUND: Money returned to the owner.
    """

    EXPENSE = "expense"
    REFUND = "refund"


class ExtractionField(StrEnum):
    """Aspect an extraction may declare explicitly unresolved.

    Declaring a field unknown is a first-class outcome: the workflow asks one
    targeted question instead of writing a guess. A description is absent from
    this set because the owner message itself always supplies one.

    Attributes:
        AMOUNT: The total could not be determined.
        OCCURRED_AT: A stated time could not be expressed unambiguously.
        MERCHANT: A referenced merchant could not be identified.
        PAYMENT: A referenced funding arrangement could not be separated.
    """

    AMOUNT = "amount"
    OCCURRED_AT = "occurred_at"
    MERCHANT = "merchant"
    PAYMENT = "payment"


class ExtractedPaymentPart(_WorkflowContract):
    """One funding portion as the owner named it, before resolution.

    The model reports the owner's own words for a funding source. Resolution to
    a stable funding-source identifier is deterministic and uses the profile's
    configured sources, so the model can neither invent a source nor choose the
    default.

    Attributes:
        funding_source_text: Verbatim funding words used by the owner.
        amount_minor_units: Positive magnitude of this portion in cents, or
            None when the owner named one source without splitting a total.
    """

    funding_source_text: NonEmptyText
    amount_minor_units: int | None = Field(default=None, gt=0)


class ExpenseExtraction(_WorkflowContract):
    """One model-produced expense or refund candidate awaiting resolution.

    Cross-field validation keeps a declared unknown and a supplied value
    mutually exclusive, so an extraction can never both claim it could not
    resolve an aspect and provide a guess for it.

    Absence is not the same as unknown. No temporal words means the movement
    occurred when the message was sent; no funding words means the profile
    default applies; no merchant means none was mentioned. Each of those is a
    deterministic default, whereas an entry in `unknowns` is a question.

    Attributes:
        polarity: Whether money left or returned to the owner.
        amount_minor_units: Positive total magnitude in cents, or None when the
            amount is declared unknown.
        currency: Operational V1 currency, present exactly when an amount is.
        occurred_at_text: Verbatim temporal words when the message states a
            time, resolved deterministically against the message timestamp.
        description: Owner-facing description of the movement.
        merchant: Merchant metadata when the message names one.
        payment_parts: Funding portions as the owner named them.
        unknowns: Aspects the model explicitly could not resolve.
        evidence: Short verbatim span supporting the extraction.
    """

    polarity: ExpensePolarity
    amount_minor_units: int | None = Field(default=None, gt=0)
    currency: Literal["EUR"] | None = None
    occurred_at_text: NonEmptyText | None = None
    description: NonEmptyText
    merchant: NonEmptyText | None = None
    payment_parts: tuple[ExtractedPaymentPart, ...] = ()
    unknowns: tuple[ExtractionField, ...] = ()
    evidence: EvidenceText

    @field_validator("unknowns")
    @classmethod
    def _reject_repeated_unknown_fields(
        cls, unknowns: tuple[ExtractionField, ...]
    ) -> tuple[ExtractionField, ...]:
        """Keep the unresolved-aspect list a set.

        Args:
            unknowns: Declared unresolved aspects.

        Returns:
            The validated unknowns.

        Raises:
            ValueError: If an aspect was declared unknown more than once.
        """

        if len(set(unknowns)) != len(unknowns):
            raise ValueError("unknowns must be distinct")
        return unknowns

    @field_validator("payment_parts")
    @classmethod
    def _validate_payment_shape(
        cls, parts: tuple[ExtractedPaymentPart, ...]
    ) -> tuple[ExtractedPaymentPart, ...]:
        """Require a split to be fully quantified and free of repeated sources.

        A single named source may omit its amount because it carries the whole
        total. Two or more portions describe a split, and a split with an
        unquantified portion could never reconcile, so it is rejected at the
        boundary instead of reaching allocation.

        Args:
            parts: Extracted funding portions.

        Returns:
            The validated portions.

        Raises:
            ValueError: If sources repeat or a split omits an amount.
        """

        named = [part.funding_source_text.casefold() for part in parts]
        if len(set(named)) != len(named):
            raise ValueError("payment parts must name distinct funding sources")
        if len(parts) > 1 and any(part.amount_minor_units is None for part in parts):
            raise ValueError("a split payment must quantify every part")
        return parts

    @model_validator(mode="after")
    def _validate_unknown_consistency(self) -> Self:
        """Keep every declared unknown mutually exclusive with its value.

        Returns:
            The validated extraction.

        Raises:
            ValueError: If a field is both declared unknown and supplied, if an
                amount lacks its currency, or if a known amount is absent.
        """

        declared = set(self.unknowns)
        if (self.amount_minor_units is None) != (ExtractionField.AMOUNT in declared):
            raise ValueError(
                "an absent amount must be declared unknown and a known amount "
                "must not be"
            )
        if (self.currency is None) != (self.amount_minor_units is None):
            raise ValueError("currency must accompany an amount")
        if ExtractionField.OCCURRED_AT in declared and (
            self.occurred_at_text is not None
        ):
            raise ValueError("an unknown occurrence time cannot also be stated")
        if ExtractionField.MERCHANT in declared and self.merchant is not None:
            raise ValueError("an unknown merchant cannot also be named")
        if ExtractionField.PAYMENT in declared and self.payment_parts:
            raise ValueError("an unknown payment cannot also be itemized")
        return self

    @property
    def is_resolvable(self) -> bool:
        """Return whether no declared unknown blocks deterministic resolution."""

        return not self.unknowns


CATEGORY_FIT_CONTRACT_VERSION = "v1"
"""Version of the category-fit model boundary.

Versioned independently of the router and extraction boundaries, and recorded
in the provenance of every category allocation it produces so a later change
stays traceable in stored history.
"""


class CategoryFitOutcome(StrEnum):
    """Whether the active taxonomy already covers a movement's purpose.

    Attributes:
        EXISTING: An active category adequately describes the purpose.
        PROPOSAL: No active category is adequate, so a candidate is offered for
            owner approval.
    """

    EXISTING = "existing"
    PROPOSAL = "proposal"


class CategoryFit(_WorkflowContract):
    """One model-produced purpose judgment awaiting deterministic resolution.

    This is a single flat schema with conditional validation rather than a
    discriminated union. A local model emits one fixed schema far more reliably
    than a `oneOf`, and no model is capability-screened for union output, so the
    branch is carried by an enum field the schema validates.

    Categories are named, never identified. Resolution to a stable identifier
    happens deterministically against the profile's active taxonomy, so a model
    cannot reference a node that does not exist or anchor history to a display
    name.

    Attributes:
        outcome: Whether an existing category fits or one is proposed.
        category_name: Active category name or alias, required for an existing
            outcome and forbidden otherwise.
        proposed_name: Candidate display name, required for a proposal.
        parent_name: Existing root category the candidate belongs under.
        description: Inclusion guidance for the candidate, required for a
            proposal.
        rationale: Why no active category is adequate, required for a proposal.
        considered_category_names: Active categories the judgment examined,
            recorded as evidence that a search happened before proposing.
        evidence: Short verbatim span of the owner message supporting the
            judgment.
    """

    outcome: CategoryFitOutcome
    category_name: NonEmptyText | None = None
    proposed_name: NonEmptyText | None = None
    parent_name: NonEmptyText | None = None
    description: NonEmptyText | None = None
    rationale: NonEmptyText | None = None
    considered_category_names: tuple[NonEmptyText, ...] = ()
    evidence: EvidenceText

    @field_validator("considered_category_names")
    @classmethod
    def _reject_repeated_candidates(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        """Keep the examined-candidate list a set.

        Args:
            names: Active categories the judgment examined.

        Returns:
            The validated names.

        Raises:
            ValueError: If a candidate is listed more than once.
        """

        folded = [" ".join(name.split()).casefold() for name in names]
        if len(set(folded)) != len(folded):
            raise ValueError("considered categories must be distinct")
        return names

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> Self:
        """Require exactly the fields the selected outcome defines.

        Returns:
            The validated judgment.

        Raises:
            ValueError: If the judgment carries fields belonging to the other
                outcome, or omits one its own outcome requires.
        """

        if self.outcome is CategoryFitOutcome.EXISTING:
            if self.category_name is None:
                raise ValueError("an existing outcome must name a category")
            if any(
                value is not None
                for value in (
                    self.proposed_name,
                    self.parent_name,
                    self.description,
                    self.rationale,
                )
            ):
                raise ValueError("an existing outcome cannot carry proposal fields")
            return self
        if self.category_name is not None:
            raise ValueError("a proposal cannot also resolve to an existing category")
        if (
            self.proposed_name is None
            or self.description is None
            or self.rationale is None
        ):
            raise ValueError(
                "a proposal requires a name, a description, and a rationale"
            )
        return self
