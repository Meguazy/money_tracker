"""Stable failure vocabulary for deterministic resolution of model output.

A resolution failure is an expected outcome, not a defect: the owner wrote
something the system cannot turn into a trusted domain value, and the workflow
must ask one targeted question instead of writing a guess. The question depends
on which step failed, so every failure carries a machine-readable reason rather
than only prose.

Reason codes are safe to log, trace, and assert on. Like routing justification
codes they deliberately carry no owner text.
"""

from __future__ import annotations

from enum import StrEnum


class ResolutionReason(StrEnum):
    """Why an extraction could not become a trusted domain value.

    Attributes:
        AMOUNT_UNKNOWN: The extraction declared the total unresolved.
        OCCURRENCE_UNKNOWN: The extraction declared the occurrence time
            unresolved.
        OCCURRENCE_UNREADABLE: Stated temporal words matched no supported
            Italian form.
        OCCURRENCE_IN_FUTURE: Stated words resolved to a day after the message.
        PAYMENT_UNKNOWN: The extraction declared the funding arrangement
            unresolved.
        NO_DEFAULT_FUNDING_SOURCE: No funding words were stated and the profile
            configures no active default source.
        FUNDING_SOURCE_UNRESOLVED: Stated funding words match no active source.
        FUNDING_SOURCE_REPEATED: Two stated portions resolve to one source.
        SPLIT_DOES_NOT_RECONCILE: Stated portions do not sum to the total.
        CATEGORY_UNRESOLVED: A named category matches no active node.
        CATEGORY_ALREADY_EXISTS: A proposed name duplicates an active name or
            alias, so an adequate node already exists.
        CATEGORY_PARENT_INVALID: A proposed parent is not an active root.
        CATEGORY_EXCLUDED: A proposed name was already rejected in this review.
        POLARITY_DISAGREEMENT: Routing and extraction disagreed about whether
            money left or returned to the owner.
        MODEL_OUTPUT_INVALID: A model boundary could not produce a schema-valid
            answer within its retry budget.
    """

    AMOUNT_UNKNOWN = "amount_unknown"
    OCCURRENCE_UNKNOWN = "occurrence_unknown"
    OCCURRENCE_UNREADABLE = "occurrence_unreadable"
    OCCURRENCE_IN_FUTURE = "occurrence_in_future"
    PAYMENT_UNKNOWN = "payment_unknown"
    NO_DEFAULT_FUNDING_SOURCE = "no_default_funding_source"
    FUNDING_SOURCE_UNRESOLVED = "funding_source_unresolved"
    FUNDING_SOURCE_REPEATED = "funding_source_repeated"
    SPLIT_DOES_NOT_RECONCILE = "split_does_not_reconcile"
    CATEGORY_UNRESOLVED = "category_unresolved"
    CATEGORY_ALREADY_EXISTS = "category_already_exists"
    CATEGORY_PARENT_INVALID = "category_parent_invalid"
    CATEGORY_EXCLUDED = "category_excluded"
    POLARITY_DISAGREEMENT = "polarity_disagreement"
    MODEL_OUTPUT_INVALID = "model_output_invalid"


class UnresolvedExtractionError(ValueError):
    """Raised when an extraction cannot become a trusted domain value.

    Attributes:
        reason: Machine-readable cause selecting the clarification to ask.
    """

    def __init__(self, reason: ResolutionReason) -> None:
        """Bind one failure to its stable reason code.

        Args:
            reason: Machine-readable cause of the failure.
        """

        super().__init__(reason.value)
        self.reason = reason
