"""Real Langfuse tests for publishing promotion evidence.

The test publishes the committed EVD-004 evidence through the production
publisher into the `integration-test` environment, reads it back through the
public API, and deletes the trace it created.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from money_tracker.evaluations.publication import publish_promotion
from money_tracker.tracing import open_evaluation_publisher
from money_tracker.tracing.langfuse import trace_id_for
from money_tracker.workflows.promotion import PROMOTED_TEXT_STACK
from tests.integration.money_tracker.tracing.store import (
    ENVIRONMENT,
    LangfuseReader,
    named,
)

pytestmark = [
    pytest.mark.langfuse,
    pytest.mark.skipif(
        os.getenv("RUN_LANGFUSE_INTEGRATION_TESTS") != "1",
        reason="set RUN_LANGFUSE_INTEGRATION_TESTS=1 with the observability profile",
    ),
]

_SCORES = Path(__file__).resolve().parents[4] / "out" / "evaluations"


def test_promotion_evidence_is_published_and_linkable_by_decision(
    langfuse: LangfuseReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish the decision, its configuration, and every run it rests on.

    The runtime switch is off, as on a workstation whose services are
    untraced: the publication command is its own explicit request.
    """

    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "false")
    publisher = open_evaluation_publisher(environment=ENVIRONMENT)
    assert publisher is not None

    outcome = publish_promotion(
        PROMOTED_TEXT_STACK, out_root=_SCORES, publisher=publisher
    )
    trace_id = trace_id_for(outcome.trace_reference)
    langfuse.created.append(trace_id)

    assert outcome.published is True
    names = {f"run:{suite}" for suite in PROMOTED_TEXT_STACK.evaluation_runs}

    def ready(observations: list[dict[str, Any]]) -> bool:
        present = {item["name"] for item in observations}
        return {"promotion:EVD-004", *names} <= present

    observations = langfuse.wait_for(trace_id, ready)
    root = named(observations, "promotion:EVD-004")
    assert root["isRootObservation"] is True
    assert root["type"] == "EVALUATOR"
    assert root["environment"] == ENVIRONMENT
    assert root["metadata"]["promotion_decision"] == "EVD-004"
    assert json.loads(root["input"])["versions"] == dict(PROMOTED_TEXT_STACK.versions)
    for suite, run_id in PROMOTED_TEXT_STACK.evaluation_runs.items():
        child = named(observations, f"run:{suite}")
        assert child["parentObservationId"] == root["id"]
        assert json.loads(child["input"])["run_id"] == run_id
        assert json.loads(child["output"])["blocking_gates_passed"] is True
