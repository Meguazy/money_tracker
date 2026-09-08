"""Tests for the summary the evaluation command prints when a run finishes."""

from __future__ import annotations

from pathlib import Path

from money_tracker.evaluations.cli import _command_summary

from .test_suites import _qwen, _report


def test_the_printed_summary_names_the_model_under_test(tmp_path: Path) -> None:
    """Answer which model produced a score at the surface a run is read on."""

    summary = _command_summary(_report(tmp_path, model=_qwen()))

    assert summary["model"] == {
        "alias": "local-model",
        "gguf_filename": "Qwen3-14B-Q5_K_M.gguf",
        "gguf_sha256": "sha256:" + "c" * 64,
        "quantization": "Q5_K_M",
        "metadata_complete": True,
    }
    assert summary["run_id"] == "ROU-20260903-000000-abcdef12"
    assert summary["suite"] == "routing/intent-it"


def test_the_printed_summary_carries_no_case_record(tmp_path: Path) -> None:
    """Keep owner text and per-case labels out of a terminal summary."""

    summary = _command_summary(_report(tmp_path))

    assert "cases" not in summary
    assert summary["metrics"] == {"macro_f1": 0.96}
    assert summary["promotion_blockers"] == [
        "dataset_below_minimum",
        "model_metadata_incomplete",
    ]
