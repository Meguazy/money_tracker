"""Tests for the summary the evaluation command prints when a run finishes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from money_tracker.evaluations.cli import (
    _command_summary,
    _parser,
    _require_declared_model,
    _run_catalog,
)
from money_tracker.evaluations.metadata import CapabilitySettings

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


def _settings(**overrides: object) -> CapabilitySettings:
    """Build endpoint settings without reading the local environment."""

    fields: dict[str, object] = {
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "local",
        "model_alias": "local-model",
        "timeout_seconds": 1.0,
        "model_path": Path("/models/Qwen3-14B-Q5_K_M.gguf"),
        "server_binary": None,
        "vision_projector_path": None,
        "context_size": 8192,
    }
    fields.update(overrides)
    return CapabilitySettings(**fields)  # type: ignore[arg-type]


def test_a_run_that_declares_no_candidate_is_left_alone() -> None:
    """Keep an exploratory run against an uncatalogued model possible."""

    assert _require_declared_model(None, _settings()) is None


def test_an_undeclared_candidate_is_refused_before_the_endpoint_is_touched() -> None:
    """Fail on a typo without waiting on a server that may not be running."""

    with pytest.raises(SystemExit, match="unknown model profile 'text-qwen3-14'"):
        _require_declared_model("text-qwen3-14", _settings())


def test_the_catalog_command_reports_every_declared_candidate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Answer which candidates exist and which are downloaded already."""

    _run_catalog(None, None)

    reported = json.loads(capsys.readouterr().out)
    identifiers = [entry["id"] for entry in reported]
    assert "text-qwen3-14b" in identifiers
    for entry in reported:
        assert entry["path"].endswith(entry["filename"])
        assert isinstance(entry["present"], bool)
        assert len(entry["sha256"]) == 64


def test_one_catalog_field_is_printed_bare_for_a_shell(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let the sweep resolve a weights path without parsing JSON."""

    _run_catalog("text-qwen3-14b", "path")

    printed = capsys.readouterr().out.strip()
    assert printed.endswith("/Qwen3-14B-Q5_K_M.gguf")
    assert "\n" not in printed


def test_a_bare_field_needs_a_candidate_and_must_exist() -> None:
    """Refuse to print one value for many candidates, or an absent one."""

    with pytest.raises(SystemExit, match="--field needs --profile"):
        _run_catalog(None, "path")

    with pytest.raises(SystemExit, match="declares no projector"):
        _run_catalog("text-qwen3-14b", "projector")


def test_human_review_and_deferral_are_mutually_exclusive() -> None:
    """Require one explicit owner disposition for the human gate."""

    parser = _parser()
    reviewed = parser.parse_args(["advice", "--review-human-usefulness"])
    deferred = parser.parse_args(["advice", "--defer-human-usefulness"])

    assert reviewed.review_human_usefulness is True
    assert reviewed.defer_human_usefulness is False
    assert deferred.review_human_usefulness is False
    assert deferred.defer_human_usefulness is True
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "advice",
                "--review-human-usefulness",
                "--defer-human-usefulness",
            ]
        )
