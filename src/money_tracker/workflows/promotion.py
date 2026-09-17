"""The promoted text configuration and the evaluation runs that promoted it.

A trace that says `candidate-router-it-v6` names a prompt; it does not say that
this prompt, with this policy and threshold on this model, is the configuration
a recorded decision promoted, nor which measured runs that decision rested on.
This module records that link once, in code, beside the defaults it describes,
so a runtime trace, a published evaluation, and the decision log can name the
same decision and the same run identifiers.

The record changes nothing a model is asked. It is data about the defaults, and
a unit test holds it to the committed published runs and to the decision log,
so it cannot silently describe a promotion that did not happen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """One recorded promotion of a model-visible configuration.

    Attributes:
        decision: Decision identifier in `docs/experiments/decision-log.md`.
        decided_on: ISO date of the decision.
        model_profile: Candidate catalog profile that was promoted.
        gguf_sha256: Digest of the promoted weights.
        versions: Version-set keys the decision pins, with their promoted
            values, named as runtime traces name them.
        evaluation_runs: Published run identifier for each suite the decision
            rests on, keyed by suite.
    """

    decision: str
    decided_on: str
    model_profile: str
    gguf_sha256: str
    versions: Mapping[str, object] = field(default_factory=dict)
    evaluation_runs: Mapping[str, str] = field(default_factory=dict)

    def linkage(self, composed: Mapping[str, object]) -> dict[str, object]:
        """Describe whether an assembly runs this promoted configuration.

        Only the pinned keys an assembly actually composes are compared, so a
        routing-only command and a read-only advice request can each run the
        promoted configuration of the boundaries they use.

        Args:
            composed: Version set of the assembly that ran.

        Returns:
            `promotion_decision`, `promotion_evaluation_runs`, and the promoted
            model profile and weights digest, which a generation's recorded
            artifact identity can be compared with, when every composed pinned
            value matches; otherwise `promotion_decision` is `none` and
            `promotion_mismatch` names the keys that differ.
        """

        compared = [key for key in self.versions if key in composed]
        mismatch = sorted(
            key for key in compared if composed[key] != self.versions[key]
        )
        if not compared or mismatch:
            return {"promotion_decision": "none", "promotion_mismatch": mismatch}
        return {
            "promotion_decision": self.decision,
            "promotion_evaluation_runs": dict(self.evaluation_runs),
            "promotion_model_profile": self.model_profile,
            "promotion_gguf_sha256": self.gguf_sha256,
        }


PROMOTED_TEXT_STACK: Final = PromotionRecord(
    decision="EVD-004",
    decided_on="2026-09-09",
    model_profile="text-qwen3-14b",
    gguf_sha256=(
        "sha256:e7c9aba1129ca2936be9eca01419d9f86af40e08caa01230d5574b34d08e3e31"
    ),
    versions=MappingProxyType(
        {
            "router_prompt": "candidate-router-it-v6",
            "routing_minimum_confidence": 0.5,
            "extraction_prompt": "candidate-extraction-it-v6",
            "category_fit_prompt": "candidate-category-fit-it-v6",
            "tool_selection_prompt": "candidate-tool-selection-it-v5",
            "read_selection_policy": "explicit-finance-scope-v1",
        }
    ),
    evaluation_runs=MappingProxyType(
        {
            "routing/intent-it": "ROU-20260909-145350-b1919d44",
            "ingestion/expense-it": "ING-20260909-150059-3f9abaac",
            "categorization/purpose-it": "CAT-20260909-150354-f2f4e790",
            "tools/selection-and-use": "TOO-20260909-150703-7b2c9501",
            "advice/grounding-it": "ADV-20260909-144755-762f53cc",
            "workflow/category-review-it": "REV-20260909-150839-7a1a525f",
        }
    ),
)
"""EVD-004: the Step 3 text configuration every runtime default composes."""


PROMOTIONS: Final = MappingProxyType(
    {PROMOTED_TEXT_STACK.decision: PROMOTED_TEXT_STACK}
)
"""Every recorded promotion by decision identifier."""


__all__ = ["PROMOTED_TEXT_STACK", "PROMOTIONS", "PromotionRecord"]
