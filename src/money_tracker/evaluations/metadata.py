"""Collect reproducibility metadata without copying secrets or private inputs."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from money_tracker import __version__
from money_tracker.config import Settings
from money_tracker.evaluations.models import (
    EnvironmentMetadata,
    ModelMetadata,
    SourceMetadata,
)


@dataclass(frozen=True, slots=True)
class CapabilitySettings:
    """Runtime settings for local OpenAI-compatible capability probes.

    Attributes:
        base_url: OpenAI-compatible API base ending in ``/v1``.
        api_key: Local server credential, excluded from representations/artifacts.
        model_alias: Model identifier sent in requests.
        timeout_seconds: Per-request timeout.
        model_path: Optional local GGUF path used only for hashing metadata.
        server_binary: Optional local server binary used for version/hash metadata.
        vision_projector_path: Optional local multimodal projector path.
        context_size: Configured server context size when known.
    """

    base_url: str
    api_key: str = field(repr=False)
    model_alias: str
    timeout_seconds: float
    model_path: Path | None
    server_binary: str | None
    vision_projector_path: Path | None
    context_size: int | None

    @classmethod
    def from_env(cls) -> CapabilitySettings:
        """Build probe settings from the same local inference configuration.

        Returns:
            Settings with optional artifact paths retained locally.

        Raises:
            ValueError: If ``LLAMA_CTX_SIZE`` is not an integer.
        """

        llm = Settings.from_env()
        context_raw = os.getenv("LLAMA_CTX_SIZE", "").strip()
        context_size = int(context_raw) if context_raw else None
        model_raw = os.getenv("MODEL_PATH", "").strip()
        projector_raw = os.getenv("MMPROJ_PATH", "").strip()
        server_binary = os.getenv("LLAMA_SERVER_BIN", "").strip() or None
        return cls(
            base_url=llm.llm_base_url,
            api_key=llm.llm_api_key,
            model_alias=llm.llm_model,
            timeout_seconds=llm.llm_timeout_seconds,
            model_path=Path(model_raw) if model_raw else None,
            server_binary=server_binary,
            vision_projector_path=Path(projector_raw) if projector_raw else None,
            context_size=context_size,
        )

    @property
    def server_root_url(self) -> str:
        """Return the inference server root rather than its ``/v1`` API prefix."""

        return re.sub(r"/v1/?$", "", self.base_url.rstrip("/"))


def sha256_file(path: Path) -> str:
    """Hash a file incrementally.

    Args:
        path: Existing regular file to hash.

    Returns:
        A lowercase SHA-256 digest with its algorithm prefix.
    """

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def sha256_text(value: str) -> str:
    """Hash a non-sensitive text contract such as a chat template.

    Args:
        value: Text whose exact identity must be recorded.

    Returns:
        A lowercase SHA-256 digest with its algorithm prefix.
    """

    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _command_output(arguments: list[str]) -> str | None:
    """Return bounded command output for a metadata-only local command."""

    try:
        completed = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr).strip()
    return (
        output.splitlines()[0][:500] if completed.returncode == 0 and output else None
    )


def collect_source_metadata(repository_root: Path) -> SourceMetadata:
    """Collect immutable source identifiers for one evaluation run.

    Args:
        repository_root: Git worktree containing ``uv.lock``.

    Returns:
        Source metadata that contains no diff content or file paths.
    """

    commit = _command_output(["git", "-C", str(repository_root), "rev-parse", "HEAD"])
    status = subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    lock_path = repository_root / "uv.lock"
    return SourceMetadata(
        git_commit=commit or "unavailable",
        working_tree_clean=status.returncode == 0 and not status.stdout.strip(),
        application_version=__version__,
        python_version=platform.python_version(),
        uv_lock_hash=sha256_file(lock_path) if lock_path.is_file() else "unavailable",
    )


def _normalized_strings(value: object) -> list[str]:
    """Normalize server capability metadata into a stable string list."""

    if isinstance(value, list):
        return sorted(str(item) for item in value)
    if isinstance(value, Mapping):
        return sorted(str(key) for key, enabled in value.items() if enabled)
    return []


def _context_size(
    properties: Mapping[str, object], configured: int | None
) -> int | None:
    """Resolve context size from stable server properties or configuration."""

    direct = properties.get("n_ctx") or properties.get("context_size")
    if isinstance(direct, int):
        return direct
    defaults = properties.get("default_generation_settings")
    if isinstance(defaults, Mapping):
        nested = defaults.get("n_ctx")
        if isinstance(nested, int):
            return nested
    return configured


def _quantization(filename: str | None, properties: Mapping[str, object]) -> str | None:
    """Resolve quantization metadata without guessing from model behavior."""

    explicit = properties.get("quantization") or properties.get("general.file_type")
    if explicit is not None:
        return str(explicit)
    if not filename:
        return None
    match = re.search(
        r"(?:^|[-_.])((?:MX|NV)FP4|I?Q\d(?:_[A-Z0-9]+)*)",
        filename,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


def collect_model_metadata(
    settings: CapabilitySettings,
    properties: Mapping[str, object] | None,
    *,
    vision_required: bool,
) -> ModelMetadata:
    """Collect exact local model, template, binary, and context identifiers.

    Args:
        settings: Probe settings containing local artifact references.
        properties: Safe JSON object returned by llama.cpp ``/props``.
        vision_required: Require an exact projector hash for metadata completeness.

    Returns:
        Model metadata with an explicit completeness decision.
    """

    props = properties or {}
    configured_model = settings.model_path
    server_model = props.get("model_path")
    filename = (
        configured_model.name
        if configured_model is not None
        else Path(str(server_model)).name
        if server_model
        else None
    )
    model_hash = (
        sha256_file(configured_model)
        if configured_model is not None and configured_model.is_file()
        else None
    )
    projector = settings.vision_projector_path
    projector_hash = (
        sha256_file(projector) if projector and projector.is_file() else None
    )
    template = props.get("chat_template_tool_use") or props.get("chat_template")
    template_hash = sha256_text(str(template)) if template else None

    binary_path: Path | None = None
    if settings.server_binary:
        resolved = shutil.which(settings.server_binary)
        candidate = Path(resolved) if resolved else Path(settings.server_binary)
        binary_path = candidate if candidate.is_file() else None
    binary_hash = sha256_file(binary_path) if binary_path else None
    server_version = (
        _command_output([str(binary_path), "--version"])
        if binary_path
        else str(props.get("build_info") or props.get("version") or "").strip() or None
    )
    context_size = _context_size(props, settings.context_size)
    quantization = _quantization(filename, props)
    metadata_complete = all(
        (
            filename,
            model_hash,
            quantization,
            template_hash,
            server_version,
            binary_hash,
            context_size,
        )
    ) and (not vision_required or projector_hash is not None)

    return ModelMetadata(
        endpoint_kind="llama.cpp",
        model_alias=settings.model_alias,
        gguf_filename=filename,
        gguf_sha256=model_hash,
        quantization=quantization,
        vision_projector_sha256=projector_hash,
        chat_template_sha256=template_hash,
        chat_template_capabilities=_normalized_strings(props.get("chat_template_caps")),
        modalities=_normalized_strings(props.get("modalities")),
        context_size=context_size,
        server_version=server_version,
        server_binary_sha256=binary_hash,
        metadata_complete=bool(metadata_complete),
    )


def collect_environment_metadata() -> EnvironmentMetadata:
    """Collect non-sensitive host and GPU metadata.

    Returns:
        Host metadata with nullable accelerator fields when NVIDIA tooling is absent.
    """

    gpu_output = _command_output(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    gpu = driver = None
    vram = None
    if gpu_output:
        parts = [part.strip() for part in gpu_output.split(",")]
        if len(parts) == 3:
            gpu, driver = parts[0], parts[1]
            try:
                vram = int(parts[2])
            except ValueError:
                vram = None
    return EnvironmentMetadata(
        os=f"{platform.system()} {platform.release()}",
        kernel=platform.version(),
        machine=platform.machine(),
        gpu=gpu,
        driver_version=driver,
        vram_mib=vram,
        timezone=os.getenv("TZ", "Europe/Rome"),
    )


def as_object_mapping(value: object) -> Mapping[str, object]:
    """Narrow a decoded JSON object for strict type checking.

    Args:
        value: Decoded JSON value.

    Returns:
        The mapping value.

    Raises:
        TypeError: If the value is not an object.
    """

    if not isinstance(value, Mapping):
        raise TypeError("expected a JSON object")
    return cast(Mapping[str, object], value)
