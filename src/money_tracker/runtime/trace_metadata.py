"""The version set and decision facts a traced workflow records.

A complete prompt in a trace is not yet an attributable one: EVD-004 promotes a
combination of prompt versions, contract versions, a routing threshold, and a
read-selection policy, and a trace must say which combination produced it.
Entry points build this set from the same values they compose the dispatcher
with, so it cannot describe a different assembly from the one that ran.

Keys are flat so that each one is a separate, filterable metadata field in the
trace store rather than one serialized blob.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

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
    WorkflowIntent,
    registry_for,
)
from money_tracker.workflows.candidates import CategoryFitCandidate, ExtractionCandidate


def workflow_version_set(
    *,
    router_prompt: PromptVersion | None = None,
    minimum_confidence: float | None = None,
    extraction: ExtractionCandidate | None = None,
    category_fit: CategoryFitCandidate | None = None,
    selection: SelectionBoundary | None = None,
    selection_policy: ReadSelectionPolicyVersion | None = None,
) -> dict[str, object]:
    """Describe the prompts, contracts, and policies a dispatcher runs with.

    Args:
        router_prompt: Router instruction version composed into the router,
            when the assembly routes at all. Browser advice names its route
            by interface and composes no router.
        minimum_confidence: Routing confidence threshold in force, with the
            router.
        extraction: Extraction boundary composed into the specialists, when any.
        category_fit: Category-fit boundary composed into the specialists.
        selection: Read-tool selection boundary composed into the read graph.
        selection_policy: Deterministic read-selection policy in force.

    Returns:
        Flat metadata naming every version that shapes a model request or the
        deterministic decision taken after it. Boundaries that are not
        composed are omitted rather than reported as defaults.
    """

    versions: dict[str, object] = {"workflow_state": WORKFLOW_STATE_VERSION}
    if router_prompt is not None:
        versions["router_prompt"] = router_prompt.id
        versions["router_contract"] = ROUTER_CONTRACT_VERSION
        versions["routing_minimum_confidence"] = minimum_confidence
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


def read_decision_metadata(
    intent: WorkflowIntent, final: Mapping[str, Any]
) -> dict[str, object]:
    """Describe a read specialist's admitted tools and grounded result.

    Shared by every entry point that runs the read graph, so a question asked
    through Telegram and one asked in the browser are filterable by the same
    keys.

    Args:
        intent: Trusted routed intent whose registry bounded the model.
        final: Final read graph state.

    Returns:
        The route's admitted tools, the request that passed the selection
        policy, the executed tool, and whether its answer is grounded in tool
        output.
    """

    return {
        "read_admitted_tools": sorted(tool.value for tool in registry_for(intent)),
        "read_tool_request": state_json_object(final.get("tool_request_json")),
        "read_executed_tool": final.get("executed_tool"),
        "read_grounded": final.get("answer_json") is not None,
        "read_clarification_reason": final.get("clarification_reason"),
    }


def state_json_object(value: object) -> dict[str, Any] | None:
    """Decode a JSON object carried in graph state, or None when absent.

    Args:
        value: A state field holding a serialized contract, or anything else.

    Returns:
        The decoded object, or None when the field is absent or not an object.
    """

    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


__all__ = ["read_decision_metadata", "state_json_object", "workflow_version_set"]
