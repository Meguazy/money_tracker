"""Versioned strict schemas for the router's model boundary.

Every value in this module is produced by a local model and is therefore
untrusted input. The schemas deliberately expose no profile, timezone, funding
source, authorization, or idempotency field: AD-006 reserves those for trusted
runtime context, so the model has no shape in which to supply one.

Deterministic validation of a parsed classification lives in
`money_tracker.workflows.routing`; this module only fixes the wire shape.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

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
