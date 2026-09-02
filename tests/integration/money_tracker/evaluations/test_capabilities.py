"""Integration tests for real local-model capability protocols."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from money_tracker.evaluations.capabilities import run_capability_suite
from money_tracker.evaluations.metadata import CapabilitySettings


@pytest.mark.inference
def test_configured_local_model_passes_capability_and_metadata_gates(
    tmp_path: Path,
) -> None:
    """Exercise the production probe client against a real inference server."""

    if os.getenv("RUN_CAPABILITY_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_CAPABILITY_INTEGRATION_TESTS=1 for real capability probes")
    repository_root = Path(__file__).resolve().parents[4]
    image_raw = os.getenv("INTEGRATION_IMAGE_PATH", "").strip()
    image_path = Path(image_raw) if image_raw else None

    result = run_capability_suite(
        CapabilitySettings.from_env(),
        dataset_path=(
            repository_root / "evals" / "datasets" / "capabilities-local-model-v1.jsonl"
        ),
        output_root=tmp_path,
        image_path=image_path,
    )

    failures = {
        probe.name: probe.details
        for probe in result.report.probes
        if probe.status.value not in {"passed", "not_applicable"}
    }
    assert result.report.model.metadata_complete, result.report.model.model_dump()
    assert result.report.blocking_gates_passed, failures
    assert (result.artifact_directory / "manifest.json").is_file()
    assert (result.artifact_directory / "scores.json").is_file()
