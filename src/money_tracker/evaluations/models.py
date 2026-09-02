"""Typed contracts for local evaluation manifests and capability reports."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ProbeStatus(StrEnum):
    """Outcome of one deterministic capability probe."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


class ProbeResult(BaseModel):
    """Machine-readable result for one local-model capability.

    Attributes:
        name: Stable capability identifier.
        status: Deterministic probe outcome.
        latency_ms: End-to-end request duration when a request was made.
        details: Small non-sensitive diagnostics suitable for committed summaries.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    status: ProbeStatus
    latency_ms: int | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class DatasetMetadata(BaseModel):
    """Identity of the immutable synthetic dataset used by a run."""

    model_config = ConfigDict(extra="forbid")

    suite: str
    dataset_id: str
    version: str
    content_hash: str
    item_count: int
    private_dataset: bool = False


class SourceMetadata(BaseModel):
    """Source and Python environment identity for a reproducible run."""

    model_config = ConfigDict(extra="forbid")

    git_commit: str
    working_tree_clean: bool
    application_version: str
    python_version: str
    uv_lock_hash: str


class ModelMetadata(BaseModel):
    """Exact local model, template, context, and server identity."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["local-openai-compatible"] = "local-openai-compatible"
    endpoint_kind: str
    model_alias: str
    gguf_filename: str | None
    gguf_sha256: str | None
    quantization: str | None
    vision_projector_sha256: str | None
    chat_template_sha256: str | None
    chat_template_capabilities: list[str]
    modalities: list[str]
    context_size: int | None
    server_version: str | None
    server_binary_sha256: str | None
    metadata_complete: bool


class EnvironmentMetadata(BaseModel):
    """Non-sensitive host and accelerator identity for a capability run."""

    model_config = ConfigDict(extra="forbid")

    os: str
    kernel: str
    machine: str
    gpu: str | None
    driver_version: str | None
    vram_mib: int | None
    timezone: str


class CapabilityReport(BaseModel):
    """Complete deterministic report emitted by the capability suite."""

    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal["1"] = "1"
    run_id: str
    started_at: str
    completed_at: str
    source: SourceMetadata
    dataset: DatasetMetadata
    model: ModelMetadata
    environment: EnvironmentMetadata
    probes: list[ProbeResult]
    blocking_gates_passed: bool
    eligible_for_promotion: bool = False
    local_artifact_path: str
    langfuse_published: bool = False


class CapabilityRunResult(BaseModel):
    """Report plus its local artifact location returned to callers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    report: CapabilityReport
    artifact_directory: Path
