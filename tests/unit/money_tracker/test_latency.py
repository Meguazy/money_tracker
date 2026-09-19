"""Unit tests for the shared nearest-rank latency distribution."""

from __future__ import annotations

from money_tracker.latency import LatencySummary, render_summary, summarize_latencies


def test_nearest_rank_percentiles_match_the_performance_requirement() -> None:
    """Pick observed values, never interpolated ones, as NFR-PER-001 states."""

    summary = summarize_latencies([400, 100, 300, 200, 20_000])

    assert summary == LatencySummary(
        sample_count=5, p50_ms=300, p95_ms=20_000, maximum_ms=20_000
    )


def test_twenty_samples_put_the_ninety_fifth_percentile_on_the_nineteenth() -> None:
    """Keep one outlier in twenty from deciding the p95 on its own."""

    summary = summarize_latencies(list(range(1, 20)) + [10_000])

    assert summary is not None
    assert summary.p95_ms == 19
    assert summary.p50_ms == 10
    assert summary.maximum_ms == 10_000


def test_no_samples_have_no_distribution() -> None:
    """Never report a percentile of nothing."""

    assert summarize_latencies([]) is None
    assert render_summary(None) is None


def test_a_distribution_renders_as_the_command_json() -> None:
    """Print the same four keys from every profiling command."""

    assert render_summary(LatencySummary(3, 10, 20, 30)) == {
        "samples": 3,
        "p50": 10,
        "p95": 20,
        "maximum": 30,
    }
