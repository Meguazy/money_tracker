"""Nearest-rank latency distributions shared by every profiling surface.

NFR-PER-001 is stated as a nearest-rank 95th percentile, and the evaluation
harness already reports latency the same way. The Telegram latency report and
the trace profile both summarize durations with the functions here, so a p95
read from the database and one read from the trace store mean the same thing.
The module depends on the standard library only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Nearest-rank distribution of a set of durations.

    Attributes:
        sample_count: Durations the distribution covers.
        p50_ms: Nearest-rank median.
        p95_ms: Nearest-rank 95th percentile.
        maximum_ms: Slowest duration.
    """

    sample_count: int
    p50_ms: int
    p95_ms: int
    maximum_ms: int


def summarize_latencies(values: Iterable[int]) -> LatencySummary | None:
    """Summarize millisecond durations with the nearest-rank convention.

    Args:
        values: Durations in any order.

    Returns:
        The distribution, or None when there is nothing to summarize.
    """

    ordered = sorted(values)
    if not ordered:
        return None
    return LatencySummary(
        sample_count=len(ordered),
        p50_ms=ordered[math.ceil(0.5 * len(ordered)) - 1],
        p95_ms=ordered[math.ceil(0.95 * len(ordered)) - 1],
        maximum_ms=ordered[-1],
    )


def render_summary(summary: LatencySummary | None) -> dict[str, int] | None:
    """Render a distribution as the JSON object every command prints.

    Args:
        summary: Distribution to render, or None without samples.

    Returns:
        Sample count, p50, p95, and maximum in milliseconds, or None.
    """

    if summary is None:
        return None
    return {
        "samples": summary.sample_count,
        "p50": summary.p50_ms,
        "p95": summary.p95_ms,
        "maximum": summary.maximum_ms,
    }


__all__ = ["LatencySummary", "render_summary", "summarize_latencies"]
