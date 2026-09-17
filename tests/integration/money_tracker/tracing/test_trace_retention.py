"""Real Langfuse tests for trace retention, private export, and deletion.

The test writes synthetic traces through the production tracer into the
`integration-test` environment on both sides of a cutoff, then drives the
production `money-tracker-traces` command against the running store: a plan,
a private export, a dry-run deletion, a confirmed deletion by age, and a
confirmed deletion by identifier, each checked by reading the store back.
Every trace it created is deleted at teardown even if an assertion fails.

Other tests in the same run write to the same environment and Langfuse deletes
their traces asynchronously, so an age-based selection may also hold their
traces. The assertions therefore require this test's old traces to be
selected, exported, and deleted, and its spanning and newer traces never to be.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from money_tracker.tracing import open_tracer
from money_tracker.tracing.cli import main
from money_tracker.tracing.langfuse import trace_id_for
from tests.integration.money_tracker.tracing.store import (
    ENVIRONMENT,
    LangfuseReader,
)

pytestmark = [
    pytest.mark.langfuse,
    pytest.mark.skipif(
        os.getenv("RUN_LANGFUSE_INTEGRATION_TESTS") != "1",
        reason="set RUN_LANGFUSE_INTEGRATION_TESTS=1 with the observability profile",
    ),
]

VERIFY_SECONDS = "300"


def _command(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    """Run the production command and return its JSON result."""

    capsys.readouterr()
    main(list(argv))
    result: dict[str, Any] = json.loads(capsys.readouterr().out)
    return result


def _wait_visible(langfuse: LangfuseReader, trace_ids: list[str], counts: list[int]):
    """Wait until every trace holds the expected number of observations."""

    for trace_id, count in zip(trace_ids, counts, strict=True):
        langfuse.wait_for(
            trace_id, lambda observed, count=count: len(observed) >= count
        )


def test_retention_plans_exports_and_deletes_by_age_and_identifier(
    langfuse: LangfuseReader,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Remove only what ended before the cutoff, and prove each removal."""

    marker = f"conservazione{uuid4().hex}"
    seeds = {name: f"integration-test:retention:{name}:{uuid4()}" for name in "ABCD"}
    ids = {name: trace_id_for(seed) for name, seed in seeds.items()}
    langfuse.created.extend(ids.values())
    tracer = open_tracer()
    for name in "AB":
        with tracer.span("retention-root", trace_seed=seeds[name], input=marker):
            with tracer.span("retention-child"):
                pass
    with tracer.span("retention-root", trace_seed=seeds["C"], input=marker):
        pass
    time.sleep(1.5)
    cutoff = datetime.now(UTC).isoformat()
    time.sleep(1.5)
    with tracer.span("retention-retry", trace_seed=seeds["C"], input=marker):
        pass
    with tracer.span("retention-root", trace_seed=seeds["D"], input=marker):
        pass
    tracer.shutdown()
    _wait_visible(langfuse, [ids[name] for name in "ABCD"], [2, 2, 2, 1])
    selection = ["--environment", ENVIRONMENT, "--before", cutoff]

    plan = _command(capsys, "plan", *selection)

    selected = {item["trace_id"] for item in plan["traces"]}
    assert {ids["A"], ids["B"]} <= selected
    assert not {ids["C"], ids["D"]} & selected
    assert plan["spanning_traces_kept"] >= 1
    assert plan["selected_observations"] >= 4

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "retention-export.jsonl"
    exported = _command(capsys, "export", *selection, "--output", str(destination))

    assert exported["exported_traces"] >= 2
    content = destination.read_bytes()
    assert exported["sha256"] == f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in content.splitlines()]
    exported_ids = {line["traceId"] for line in lines}
    assert {ids["A"], ids["B"]} <= exported_ids
    assert not {ids["C"], ids["D"]} & exported_ids
    assert marker in content.decode("utf-8")
    assert str(destination) not in json.dumps(exported)

    dry_run = _command(capsys, "delete", *selection)

    assert dry_run["dry_run"] is True
    assert langfuse.observations(ids["A"])

    deleted = _command(
        capsys, "delete", *selection, "--confirm", "--verify-seconds", VERIFY_SECONDS
    )

    assert {ids["A"], ids["B"]} <= set(deleted["deleted_verified"])
    assert not {ids["C"], ids["D"]} & set(deleted["deleted_verified"])
    assert deleted["remaining"] == []
    assert langfuse.observations(ids["A"]) == []
    assert langfuse.observations(ids["B"]) == []
    assert len(langfuse.observations(ids["C"])) == 2
    assert len(langfuse.observations(ids["D"])) == 1

    by_identifier = _command(
        capsys,
        "delete",
        "--trace-id",
        ids["C"],
        "--trace-id",
        ids["D"],
        "--confirm",
        "--verify-seconds",
        VERIFY_SECONDS,
    )

    assert sorted(by_identifier["deleted_verified"]) == sorted([ids["C"], ids["D"]])
    assert by_identifier["remaining"] == []
    assert langfuse.observations(ids["C"]) == []
    assert langfuse.matching_input(marker) == []
