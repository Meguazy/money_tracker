"""Content-free latency profile of the traces in one window.

The database measures NFR-PER-001 end to end, and splits it only at the
worker's completion commit. Traces add what the database cannot see: when the
worker actually picked an update up, how long each attempt took, and how long
every model call and graph node inside it ran. This module turns observation
names and times into that profile. It never sees an input, an output, or
metadata, because the reader it is given requests none.

A traced Telegram update is decomposed at its stage spans:

- wait: from the end of `telegram-ingest` to the start of the last
  `work-unit`, covering queueing, earlier failed attempts, and any time the
  worker or model was unavailable;
- processing: the last `work-unit` span;
- delivery: from the end of that span to the end of the first
  `telegram-delivery` that started after it began.

The delivery stage opened for a control revocation carries its operation only
in its input, which the profile never reads. When a revocation is delivered
before the answer it accompanies, the answer's delivery is therefore
under-measured by at most one Bot API call.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from money_tracker.latency import LatencySummary, summarize_latencies
from money_tracker.tracing.ports import (
    TELEGRAM_DELIVERY_STAGE,
    TELEGRAM_INGEST_STAGE,
    WORK_UNIT_STAGE,
)
from money_tracker.tracing.retention import ObservationTime


@dataclass(frozen=True, slots=True)
class ObservationProfile:
    """Duration distribution of every finished observation of one kind and name.

    Attributes:
        kind: Observation type the store reports.
        name: Stable observation name.
        durations: Distribution of start-to-end durations.
    """

    kind: str
    name: str
    durations: LatencySummary


@dataclass(frozen=True, slots=True)
class UpdateProfile:
    """Stage decomposition of the traced Telegram updates in a window.

    Attributes:
        complete: Updates with an ingest, a finished attempt, and a delivery.
        incomplete: Updates missing a finished attempt or a delivery.
        retried: Complete updates that needed more than one attempt.
        wait: Ingest-to-last-attempt distribution.
        processing: Last-attempt distribution.
        delivery: Attempt-end-to-delivery-end distribution.
        total: Ingest-start-to-delivery-end distribution.
    """

    complete: int
    incomplete: int
    retried: int
    wait: LatencySummary | None
    processing: LatencySummary | None
    delivery: LatencySummary | None
    total: LatencySummary | None


@dataclass(frozen=True, slots=True)
class TraceProfile:
    """Everything the profile of one window reports.

    Attributes:
        environment: Langfuse environment profiled.
        since: Inclusive lower bound on observation start.
        until: Exclusive upper bound, or None for up to now.
        observation_count: Observations read in the window.
        observations: Per kind and name distributions, ordered by name.
        updates: Telegram update stage decomposition.
    """

    environment: str
    since: datetime
    until: datetime | None
    observation_count: int
    observations: tuple[ObservationProfile, ...]
    updates: UpdateProfile


def profile_timings(
    timings: Sequence[ObservationTime],
    *,
    environment: str,
    since: datetime,
    until: datetime | None,
) -> TraceProfile:
    """Build the latency profile of one window's observation times.

    Args:
        timings: Content-free observation times read from the store.
        environment: Environment they were read from.
        since: Inclusive lower bound they were read with.
        until: Exclusive upper bound, or None.

    Returns:
        Per-name distributions and the Telegram update decomposition.
    """

    durations: dict[tuple[str, str], list[int]] = {}
    for timing in timings:
        if timing.ended_at is not None:
            durations.setdefault((timing.kind, timing.name), []).append(
                _milliseconds(timing.ended_at - timing.started_at)
            )
    observations = tuple(
        ObservationProfile(kind=kind, name=name, durations=summary)
        for (kind, name), values in sorted(
            durations.items(), key=lambda item: (item[0][1], item[0][0])
        )
        if (summary := summarize_latencies(values)) is not None
    )
    return TraceProfile(
        environment=environment,
        since=since,
        until=until,
        observation_count=len(timings),
        observations=observations,
        updates=_update_profile(timings),
    )


@dataclass(frozen=True, slots=True)
class _UpdateTiming:
    """Stage durations of one complete traced update."""

    wait_ms: int
    processing_ms: int
    delivery_ms: int
    total_ms: int
    attempts: int


def _update_profile(timings: Iterable[ObservationTime]) -> UpdateProfile:
    """Decompose every traced update that reached the gateway."""

    stages = {TELEGRAM_INGEST_STAGE, WORK_UNIT_STAGE, TELEGRAM_DELIVERY_STAGE}
    traces: dict[str, list[ObservationTime]] = {}
    for timing in timings:
        if timing.name in stages:
            traces.setdefault(timing.trace_id, []).append(timing)
    complete: list[_UpdateTiming] = []
    incomplete = 0
    for spans in traces.values():
        if not any(span.name == TELEGRAM_INGEST_STAGE for span in spans):
            continue
        decomposed = _decompose(spans)
        if decomposed is None:
            incomplete += 1
        else:
            complete.append(decomposed)
    return UpdateProfile(
        complete=len(complete),
        incomplete=incomplete,
        retried=sum(1 for item in complete if item.attempts > 1),
        wait=summarize_latencies(item.wait_ms for item in complete),
        processing=summarize_latencies(item.processing_ms for item in complete),
        delivery=summarize_latencies(item.delivery_ms for item in complete),
        total=summarize_latencies(item.total_ms for item in complete),
    )


def _decompose(spans: Sequence[ObservationTime]) -> _UpdateTiming | None:
    """Split one update's stages, or return None if a stage never finished."""

    ingest = min(
        (span for span in spans if span.name == TELEGRAM_INGEST_STAGE),
        key=lambda span: span.started_at,
    )
    attempts = sorted(
        (span for span in spans if span.name == WORK_UNIT_STAGE),
        key=lambda span: span.started_at,
    )
    if ingest.ended_at is None or not attempts or attempts[-1].ended_at is None:
        return None
    last = attempts[-1]
    assert last.ended_at is not None
    deliveries = sorted(
        (
            span
            for span in spans
            if span.name == TELEGRAM_DELIVERY_STAGE
            and span.started_at >= last.started_at
            and span.ended_at is not None
        ),
        key=lambda span: span.started_at,
    )
    if not deliveries:
        return None
    answer = deliveries[0]
    assert answer.ended_at is not None
    return _UpdateTiming(
        wait_ms=_milliseconds(last.started_at - ingest.ended_at),
        processing_ms=_milliseconds(last.ended_at - last.started_at),
        delivery_ms=_milliseconds(answer.ended_at - last.ended_at),
        total_ms=_milliseconds(answer.ended_at - ingest.started_at),
        attempts=len(attempts),
    )


def _milliseconds(elapsed: timedelta) -> int:
    """Round an elapsed time to whole milliseconds, never below zero.

    The gateway and the worker read the clock separately, so a stage boundary
    one process recorded can precede the other's by a few milliseconds.
    """

    return max(0, round(elapsed.total_seconds() * 1000))


__all__ = [
    "ObservationProfile",
    "TraceProfile",
    "UpdateProfile",
    "profile_timings",
]
