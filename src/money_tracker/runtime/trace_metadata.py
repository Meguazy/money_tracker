"""The version set a traced dispatch records beside its payloads.

A complete prompt in a trace is not yet an attributable one: EVD-004 promotes a
combination of prompt versions, contract versions, a routing threshold, and a
read-selection policy, and a trace must say which combination produced it.
Entry points build this set from the same values they compose the dispatcher
with, so it cannot describe a different assembly from the one that ran.

Keys are flat so that each one is a separate, filterable metadata field in the
trace store rather than one serialized blob.
"""

from __future__ import annotations

from money_tracker.domain.taxonomy_seed import TAXONOMY_VERSION
from money_tracker.runtime.read_graph import SelectionBoundary
from money_tracker.workflows import (
    CATEGORY_FIT_CONTRACT_VERSION,
    EXTRACTION_CONTRACT_VERSION,
    ROUTER_CONTRACT_VERSION,
    TOOL_CONTRACT_VERSION,
    WORKFLOW_STATE_VERSION,
    PromptVersion,
    ReadSelectionPolicyVersion,
)
from money_tracker.workflows.candidates import CategoryFitCandidate, ExtractionCandidate


def workflow_version_set(
    *,
    router_prompt: PromptVersion,
    minimum_confidence: float,
    extraction: ExtractionCandidate | None = None,
    category_fit: CategoryFitCandidate | None = None,
    selection: SelectionBoundary | None = None,
    selection_policy: ReadSelectionPolicyVersion | None = None,
) -> dict[str, object]:
    """Describe the prompts, contracts, and policies a dispatcher runs with.

    Args:
        router_prompt: Router instruction version composed into the router.
        minimum_confidence: Routing confidence threshold in force.
        extraction: Extraction boundary composed into the specialists, when any.
        category_fit: Category-fit boundary composed into the specialists.
        selection: Read-tool selection boundary composed into the read graph.
        selection_policy: Deterministic read-selection policy in force.

    Returns:
        Flat metadata naming every version that shapes a model request or the
        deterministic decision taken after it. Boundaries that are not
        composed are omitted rather than reported as defaults.
    """

    versions: dict[str, object] = {
        "router_prompt": router_prompt.id,
        "router_contract": ROUTER_CONTRACT_VERSION,
        "routing_minimum_confidence": minimum_confidence,
        "workflow_state": WORKFLOW_STATE_VERSION,
    }
    if extraction is not None:
        versions["extraction_prompt"] = extraction.version
        versions["extraction_contract"] = EXTRACTION_CONTRACT_VERSION
        versions["extraction_schema"] = extraction.schema.__name__
    if category_fit is not None:
        versions["category_fit_prompt"] = category_fit.version
        versions["category_fit_contract"] = CATEGORY_FIT_CONTRACT_VERSION
        versions["taxonomy_seed"] = TAXONOMY_VERSION
    if selection is not None:
        versions["tool_selection_prompt"] = selection.version
        versions["tool_contract"] = TOOL_CONTRACT_VERSION
        versions["tool_selection_schema"] = selection.schema.__name__
    if selection_policy is not None:
        versions["read_selection_policy"] = selection_policy.value
    return versions


__all__ = ["workflow_version_set"]
