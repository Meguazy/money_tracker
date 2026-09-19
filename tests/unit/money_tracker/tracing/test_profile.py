"""Unit tests for the content-free trace latency profile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from money_tracker.tracing import (
    TELEGRAM_DELIVERY_STAGE,
    TELEGRAM_INGEST_STAGE,
    WORK_UNIT_STAGE,
)
from money_tracker.tracing.profile import TraceProfile, profile_timings
from money_tracker.tracing.retention import ObservationTime

_START = datetime(2026, 9, 19, 12, tzinfo=UTC)


def _span(
    trace: str,
    name: str,
    start_ms: int,
    end_ms: int | None,
    *,
    kind: str = "SPAN",
) -> ObservationTime:
    """Build one observation time relative to the window start."""

    return ObservationTime(
        trace_id=trace,
        kind=kind,
        name=name,
        started_at=_START + timedelta(milliseconds=start_ms),
        ended_at=None if end_ms is None else _START + timedelta(milliseconds=end_ms),
    )


def _profile(*timings: ObservationTime) -> TraceProfile:
    """Profile synthetic times in the development environment."""

    return profile_timings(timings, environment="development", since=_START, until=None)


def test_an_update_splits_into_wait_processing_and_delivery() -> None:
    """Attribute each part of the owner's wait to the stage that caused it."""

    profile = _profile(
        _span("a", TELEGRAM_INGEST_STAGE, 0, 5),
        _span("a", WORK_UNIT_STAGE, 505, 3_505),
        _span("a", "model:RouteClassification", 600, 1_400, kind="GENERATION"),
        _span("a", TELEGRAM_DELIVERY_STAGE, 3_600, 3_900),
    )

    updates = profile.updates
    assert (updates.complete, updates.incomplete, updates.retried) == (1, 0, 0)
    assert updates.wait is not None and updates.wait.p95_ms == 500
    assert updates.processing is not None and updates.processing.p95_ms == 3_000
    assert updates.delivery is not None and updates.delivery.p95_ms == 395
    assert updates.total is not None and updates.total.p95_ms == 3_900


def test_a_retried_update_waits_through_its_failed_attempts() -> None:
    """Count backoff and the failure notice as wait, and time the last attempt."""

    profile = _profile(
        _span("b", TELEGRAM_INGEST_STAGE, 0, 10),
        _span("b", WORK_UNIT_STAGE, 10, 1_010),
        _span("b", TELEGRAM_DELIVERY_STAGE, 1_100, 1_300),
        _span("b", WORK_UNIT_STAGE, 3_010, 5_010),
        _span("b", TELEGRAM_DELIVERY_STAGE, 5_100, 5_400),
    )

    updates = profile.updates
    assert updates.retried == 1
    assert updates.wait is not None and updates.wait.p95_ms == 3_000
    assert updates.processing is not None and updates.processing.p95_ms == 2_000
    assert updates.delivery is not None and updates.delivery.p95_ms == 390


def test_unfinished_updates_and_traces_without_ingest_are_not_timed() -> None:
    """Report an update still in flight, and ignore a local route entirely."""

    profile = _profile(
        _span("c", TELEGRAM_INGEST_STAGE, 0, 1),
        _span("c", WORK_UNIT_STAGE, 100, None),
        _span("d", TELEGRAM_INGEST_STAGE, 0, 1),
        _span("d", WORK_UNIT_STAGE, 100, 900),
        _span("admin", WORK_UNIT_STAGE, 0, 700),
    )

    assert profile.updates.complete == 0
    assert profile.updates.incomplete == 2
    assert profile.updates.total is None


def test_observations_are_grouped_by_kind_and_name_and_open_ones_skipped() -> None:
    """Profile every finished call without anything it carried."""

    profile = _profile(
        _span("e", "model:WireExpenseExtraction", 0, 2_000, kind="GENERATION"),
        _span("f", "model:WireExpenseExtraction", 0, 3_000, kind="GENERATION"),
        _span("g", "model:WireExpenseExtraction", 0, None, kind="GENERATION"),
        _span("e", "extract", 0, 2_100, kind="CHAIN"),
    )

    assert profile.observation_count == 4
    assert [(item.kind, item.name) for item in profile.observations] == [
        ("CHAIN", "extract"),
        ("GENERATION", "model:WireExpenseExtraction"),
    ]
    extraction = profile.observations[1].durations
    assert (extraction.sample_count, extraction.p50_ms, extraction.maximum_ms) == (
        2,
        2_000,
        3_000,
    )


def test_a_stage_the_other_process_clocked_early_is_not_negative() -> None:
    """Clamp boundaries two processes recorded with separate clocks."""

    profile = _profile(
        _span("h", TELEGRAM_INGEST_STAGE, 0, 50),
        _span("h", WORK_UNIT_STAGE, 40, 1_040),
        _span("h", TELEGRAM_DELIVERY_STAGE, 1_030, 1_035),
    )

    assert profile.updates.wait is not None and profile.updates.wait.p95_ms == 0
    assert profile.updates.delivery is not None
    assert profile.updates.delivery.p95_ms == 0
