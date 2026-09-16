"""Tests for resolving the identity of the model artifact that answered."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from money_tracker.config import Settings
from money_tracker.tracing.identity import ArtifactDigests, ModelIdentityResolver

SETTINGS = Settings(
    llm_base_url="http://127.0.0.1:8080/v1",
    llm_api_key="local-key",
    llm_model="local-model",
    llm_temperature=0.1,
    llm_timeout_seconds=120.0,
)


def _digest(content: bytes) -> str:
    """Return the prefixed SHA-256 the resolver reports."""

    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def test_a_digest_is_measured_and_persisted_privately(tmp_path: Path) -> None:
    """Hash once, then remember the revision in an owner-only cache file."""

    weights = tmp_path / "Qwen3-14B-Q5_K_M.gguf"
    weights.write_bytes(b"weights")
    cache = tmp_path / "cache" / "artifact-digests.json"

    measured = ArtifactDigests(cache).digest(weights)

    assert measured == _digest(b"weights")
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert stored[str(weights.resolve())]["sha256"] == measured
    assert cache.stat().st_mode & 0o777 == 0o600
    assert cache.parent.stat().st_mode & 0o777 == 0o700


def test_a_remembered_revision_is_not_hashed_again(tmp_path: Path) -> None:
    """Trust a cache entry whose size, time, and inode still match the file.

    The entry deliberately names a digest the content does not have: returning
    it proves no ten-gigabyte read happened.
    """

    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"weights")
    stat = weights.stat()
    cache = tmp_path / "artifact-digests.json"
    cache.write_text(
        json.dumps(
            {
                str(weights.resolve()): {
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "inode": stat.st_ino,
                    "device": stat.st_dev,
                    "sha256": "sha256:remembered",
                }
            }
        ),
        encoding="utf-8",
    )

    assert ArtifactDigests(cache).digest(weights) == "sha256:remembered"


def test_a_changed_file_is_measured_again(tmp_path: Path) -> None:
    """Discard an entry once the file on disk is a different revision."""

    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"old")
    cache = tmp_path / "artifact-digests.json"
    digests = ArtifactDigests(cache)
    assert digests.digest(weights) == _digest(b"old")

    weights.write_bytes(b"new weights")
    os.utime(weights, ns=(1, 1))

    assert ArtifactDigests(cache).digest(weights) == _digest(b"new weights")


def test_an_unwritable_cache_still_yields_the_digest(tmp_path: Path) -> None:
    """Keep working under a read-only home, remembering in memory only."""

    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"weights")
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    assert ArtifactDigests(blocked / "digests.json").digest(weights) == _digest(
        b"weights"
    )


def test_a_missing_artifact_has_no_digest(tmp_path: Path) -> None:
    """Report absence rather than inventing an identity."""

    assert ArtifactDigests(None).digest(tmp_path / "absent.gguf") is None


class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        """Start at zero."""

        self.now = 0.0

    def __call__(self) -> float:
        """Return the current reading."""

        return self.now


def _resolver(
    tmp_path: Path,
    reports: list[Mapping[str, object] | None],
    clock: _Clock,
) -> tuple[ModelIdentityResolver, list[int]]:
    """Build a resolver whose server report is a scripted sequence."""

    reads: list[int] = []

    def properties() -> Mapping[str, object] | None:
        reads.append(1)
        return reports[min(len(reads), len(reports)) - 1]

    binary = tmp_path / "llama-server"
    binary.write_bytes(b"server")
    return (
        ModelIdentityResolver(
            settings=SETTINGS,
            properties=properties,
            digests=ArtifactDigests(None),
            server_binary=str(binary),
            configured_context_size=4096,
            clock=clock,
        ),
        reads,
    )


def _report(weights: Path) -> dict[str, object]:
    """Render a llama.cpp `/props` report for one weights file."""

    return {
        "model_path": str(weights),
        "chat_template": "{{ messages }}",
        "chat_template_caps": {"supports_tools": True, "supports_typed_content": False},
        "modalities": {"vision": False},
        "default_generation_settings": {"n_ctx": 8192},
        "build_info": "b1-3466812",
    }


def test_identity_uses_the_capability_report_fields(tmp_path: Path) -> None:
    """Name the exact artifact the server reports, with the report's own fields."""

    weights = tmp_path / "Qwen3-14B-Q5_K_M.gguf"
    weights.write_bytes(b"weights")
    resolver, _reads = _resolver(tmp_path, [_report(weights)], _Clock())

    identity = resolver()

    assert identity["model_alias"] == "local-model"
    assert identity["gguf_filename"] == "Qwen3-14B-Q5_K_M.gguf"
    assert identity["gguf_sha256"] == _digest(b"weights")
    assert identity["quantization"] == "Q5_K_M"
    assert identity["chat_template_sha256"] == (
        f"sha256:{hashlib.sha256(b'{{ messages }}').hexdigest()}"
    )
    assert identity["chat_template_capabilities"] == ["supports_tools"]
    assert identity["context_size"] == 8192
    assert identity["server_version"] == "b1-3466812"
    assert identity["server_binary_sha256"] == _digest(b"server")
    assert identity["metadata_complete"] is True
    assert str(tmp_path) not in json.dumps(identity)


def test_the_server_report_is_reused_then_refreshed(tmp_path: Path) -> None:
    """Notice a restarted server with other weights after the refresh interval."""

    first = tmp_path / "first.Q4_K_M.gguf"
    second = tmp_path / "second.Q8_0.gguf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    clock = _Clock()
    resolver, reads = _resolver(tmp_path, [_report(first), _report(second)], clock)

    assert resolver()["gguf_filename"] == first.name
    clock.now = 59.0
    assert resolver()["gguf_filename"] == first.name
    clock.now = 61.0
    assert resolver()["gguf_filename"] == second.name
    assert len(reads) == 2


def test_an_unreadable_server_is_incomplete_and_retried_sooner(
    tmp_path: Path,
) -> None:
    """Say the identity is incomplete instead of guessing it from configuration."""

    weights = tmp_path / "model.Q5_K_M.gguf"
    weights.write_bytes(b"weights")
    clock = _Clock()
    resolver, reads = _resolver(tmp_path, [None, _report(weights)], clock)

    assert resolver() == {"model_alias": "local-model", "metadata_complete": False}
    clock.now = 6.0
    assert resolver()["metadata_complete"] is True
    assert len(reads) == 2


@pytest.mark.parametrize("missing", ["chat_template", "build_info"])
def test_missing_server_facts_make_identity_incomplete(
    tmp_path: Path, missing: str
) -> None:
    """Apply the capability report's completeness rule unchanged."""

    weights = tmp_path / "model.Q5_K_M.gguf"
    weights.write_bytes(b"weights")
    report = _report(weights)
    del report[missing]
    resolver, _reads = _resolver(tmp_path, [report], _Clock())

    assert resolver()["metadata_complete"] is False
