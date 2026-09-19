"""Real Langfuse test for the content-free trace latency profile.

The test writes one synthetic Telegram update through the production tracer
into the `integration-test` environment, with the three stage spans the
gateway and worker open and a uniquely named span inside the work unit, then
drives the production `money-tracker-traces profile` command against the
running store. Other tests may write to the same environment in the same run,
so update counts are asserted as lower bounds while the uniquely named span is
asserted exactly. The trace is deleted at teardown.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from money_tracker.tracing import (
    TELEGRAM_DELIVERY_STAGE,
    TELEGRAM_INGEST_STAGE,
    TELEGRAM_UPDATE_TRACE,
    WORK_UNIT_STAGE,
    open_tracer,
)
from money_tracker.tracing.cli import main
from money_tracker.tracing.langfuse import trace_id_for
from money_tracker.tracing.retention import TraceStore
from money_tracker.tracing.settings import TracingSettings
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


def test_profile_times_stages_and_names_without_reading_content(
    langfuse: LangfuseReader,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Decompose a real traced update and time a named span, content-free."""

    marker = f"contenuto-riservato-{uuid4().hex}"
    probe = f"profile-probe-{uuid4().hex[:12]}"
    seed = f"integration-test:profile:{uuid4()}"
    trace_id = trace_id_for(seed)
    langfuse.created.append(trace_id)
    since = datetime.now(UTC) - timedelta(seconds=1)
    tracer = open_tracer()
    stage = {"trace_seed": seed, "trace_name": TELEGRAM_UPDATE_TRACE}
    with tracer.span(TELEGRAM_INGEST_STAGE, input=marker, **stage):
        pass
    time.sleep(0.3)
    with tracer.span(WORK_UNIT_STAGE, input=marker, **stage):
        time.sleep(0.2)
        with tracer.span(probe, input=marker):
            time.sleep(0.3)
    with tracer.span(TELEGRAM_DELIVERY_STAGE, input=marker, **stage):
        time.sleep(0.1)
    tracer.shutdown()
    langfuse.wait_for(trace_id, lambda observed: len(observed) >= 4)

    timings = TraceStore(TracingSettings.for_command(environment=ENVIRONMENT)).timings(
        environment=ENVIRONMENT, since=since
    )
    capsys.readouterr()
    main(["profile", "--environment", ENVIRONMENT, "--since", since.isoformat()])
    captured = capsys.readouterr()
    profile: dict[str, Any] = json.loads(captured.out)

    ours = {timing.name for timing in timings if timing.trace_id == trace_id}
    assert ours == {
        TELEGRAM_INGEST_STAGE,
        WORK_UNIT_STAGE,
        probe,
        TELEGRAM_DELIVERY_STAGE,
    }
    assert all(
        timing.ended_at is not None for timing in timings if timing.trace_id == trace_id
    )
    (probed,) = [item for item in profile["observations"] if item["name"] == probe]
    assert probed["samples"] == 1
    assert 250 <= probed["p50"] < 5_000
    updates = profile["telegram_updates"]
    assert updates["complete"] >= 1
    assert updates["wait_ms"]["maximum"] >= 250
    assert updates["processing_ms"]["maximum"] >= 450
    assert marker not in captured.out + captured.err
