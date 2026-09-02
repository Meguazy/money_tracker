"""Unit tests for safe and reproducible evaluation metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from money_tracker.evaluations.capabilities import run_capability_suite
from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    _quantization,
    collect_model_metadata,
    sha256_file,
)
from money_tracker.evaluations.models import ProbeStatus


def test_sha256_file_is_incremental_and_prefixed(tmp_path: Path) -> None:
    """Return the exact digest format required by run manifests."""

    artifact = tmp_path / "artifact.gguf"
    artifact.write_bytes(b"synthetic-model-content")

    expected = hashlib.sha256(b"synthetic-model-content").hexdigest()
    assert sha256_file(artifact) == f"sha256:{expected}"


def test_complete_model_metadata_hashes_local_artifacts(tmp_path: Path) -> None:
    """Record exact GGUF, binary, template, context, and quantization identity."""

    model_path = tmp_path / "synthetic-model-Q4_K_M.gguf"
    model_path.write_bytes(b"synthetic-gguf")
    server_path = tmp_path / "llama-server"
    server_path.write_text(
        "#!/usr/bin/env sh\nprintf 'llama.cpp build synthetic-123\\n'\n",
        encoding="utf-8",
    )
    server_path.chmod(0o700)
    settings = CapabilitySettings(
        base_url="http://127.0.0.1:8080/v1",
        api_key="private-local-key",
        model_alias="synthetic-model",
        timeout_seconds=1,
        model_path=model_path,
        server_binary=str(server_path),
        vision_projector_path=None,
        context_size=8192,
    )

    metadata = collect_model_metadata(
        settings,
        {
            "chat_template": "{{ messages }}",
            "chat_template_caps": {"tools": True, "system_role": True},
            "modalities": ["text"],
            "n_ctx": 8192,
        },
        vision_required=False,
    )

    assert metadata.metadata_complete
    assert metadata.gguf_filename == model_path.name
    assert metadata.gguf_sha256 == sha256_file(model_path)
    assert metadata.server_binary_sha256 == sha256_file(server_path)
    assert metadata.chat_template_sha256 is not None
    assert metadata.quantization == "Q4_K_M"
    assert "private-local-key" not in repr(settings)


def test_quantization_recognizes_dense_and_native_moe_formats() -> None:
    """Identify quantizations used by the pinned local candidate set."""

    assert _quantization("Qwen3-14B-Q5_K_M.gguf", {}) == "Q5_K_M"
    assert _quantization("gpt-oss-20b-MXFP4.gguf", {}) == "MXFP4"


def test_unavailable_server_emits_explicit_failed_artifacts(tmp_path: Path) -> None:
    """Report an absent real endpoint instead of silently skipping the suite."""

    repository_root = Path(__file__).resolve().parents[4]
    settings = CapabilitySettings(
        base_url="http://127.0.0.1:1/v1",
        api_key="must-not-appear",
        model_alias="unavailable-model",
        timeout_seconds=0.2,
        model_path=None,
        server_binary=None,
        vision_projector_path=None,
        context_size=8192,
    )

    result = run_capability_suite(
        settings,
        dataset_path=(
            repository_root
            / "evals"
            / "datasets"
            / "capabilities-local-model-v1.1.jsonl"
        ),
        output_root=tmp_path,
    )

    assert not result.report.blocking_gates_passed
    assert result.report.dataset.version == "1.1.0"
    assert not result.report.model.metadata_complete
    statuses = {probe.name: probe.status for probe in result.report.probes}
    assert statuses["server_metadata"] is ProbeStatus.ERROR
    assert statuses["image_input"] is ProbeStatus.NOT_APPLICABLE
    manifest_path = result.artifact_directory / "manifest.json"
    scores_path = result.artifact_directory / "scores.json"
    assert manifest_path.is_file()
    assert scores_path.is_file()
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "must-not-appear" not in manifest_text
    assert json.loads(manifest_text)["blocking_gates_passed"] is False
