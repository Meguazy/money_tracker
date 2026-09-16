"""Name the exact model artifact behind every traced call.

A trace that says only `local-model` cannot tell two candidates apart, and the
alias never changes when the weights do. Each traced call therefore carries the
fields the capability report already defines, assembled by the same function
and judged complete by the same rule: weights filename and SHA-256,
quantization, chat-template digest, context size, server build, and server
executable digest. The temperature and timeout travel with each request.

Two costs shape this module. Hashing the promoted weights reads about ten
gigabytes, so a digest is measured once per file revision and remembered by
path, size, modification time, and inode, in memory and in a private cache
file when the process may write one. And the server's own report is read from
its `/props` endpoint, never from configuration, because the question is what
answered rather than what was meant to; the report is re-read after a bounded
interval so a restarted server with other weights is noticed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

import httpx

from money_tracker.config import Settings
from money_tracker.evaluations.metadata import (
    as_object_mapping,
    model_metadata_from_properties,
    resolve_server_binary,
    sha256_file,
)
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger(__name__)

PROPERTIES_REFRESH_SECONDS: Final = 60.0
"""How long a successful server report is reused before it is read again."""

PROPERTIES_RETRY_SECONDS: Final = 5.0
"""How long an unavailable server report is remembered before retrying."""

PROPERTIES_TIMEOUT_SECONDS: Final = 2.0
"""Upper bound on one `/props` read."""

PropertiesReader = Callable[[], Mapping[str, object] | None]


def default_digest_cache_file() -> Path:
    """Return the private per-user digest cache location.

    Returns:
        `$XDG_CACHE_HOME/money-tracker/artifact-digests.json`, defaulting the
        cache home to `~/.cache`.
    """

    cache_home = os.getenv("XDG_CACHE_HOME", "").strip()
    base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return base / "money-tracker" / "artifact-digests.json"


class ArtifactDigests:
    """Measure a local artifact's SHA-256 once per file revision."""

    def __init__(self, cache_file: Path | None) -> None:
        """Bind the optional persistent cache.

        Args:
            cache_file: Private JSON cache shared by local processes, or None
                to remember digests only for this process.
        """

        self._cache_file = cache_file
        self._memory: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()

    def digest(self, path: Path) -> str | None:
        """Return the digest of the file currently at `path`.

        Args:
            path: Artifact path, normally the one the server reports loading.

        Returns:
            The `sha256:`-prefixed digest, or None when the path is not a
            readable regular file.
        """

        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
        except OSError:
            _LOGGER.warning("tracing.identity.artifact_unreadable", artifact=path.name)
            return None
        key = str(resolved)
        revision: dict[str, object] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "inode": stat.st_ino,
            "device": stat.st_dev,
        }
        with self._lock:
            known = self._memory.get(key) or self._load().get(key)
            if known is not None and _same_revision(known, revision):
                return str(known["sha256"])
            try:
                with _LOGGER.timed("tracing.identity.digest", artifact=resolved.name):
                    measured = sha256_file(resolved)
            except OSError:
                return None
            entry = {**revision, "sha256": measured}
            self._memory[key] = entry
            self._store(key, entry)
            return measured

    def _load(self) -> dict[str, dict[str, object]]:
        """Read the persistent cache, treating anything unreadable as empty."""

        if self._cache_file is None:
            return {}
        try:
            decoded = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(decoded, dict):
            return {}
        return {
            str(key): value
            for key, value in decoded.items()
            if isinstance(value, dict) and isinstance(value.get("sha256"), str)
        }

    def _store(self, key: str, entry: dict[str, object]) -> None:
        """Persist one measured digest atomically with owner-only permissions."""

        if self._cache_file is None:
            return
        entries = self._load()
        entries[key] = entry
        temporary = self._cache_file.with_suffix(f".{os.getpid()}.tmp")
        try:
            self._cache_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, sort_keys=True)
            temporary.replace(self._cache_file)
        except OSError:
            # A read-only home is normal for a hardened user service; the
            # digest is still remembered for the life of this process.
            _LOGGER.warning("tracing.identity.digest_cache_unwritable")
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)


def _same_revision(known: Mapping[str, object], revision: Mapping[str, object]) -> bool:
    """Tell whether a remembered digest describes the file as it is now."""

    return all(known.get(name) == value for name, value in revision.items())


def llama_properties_reader(settings: Settings) -> PropertiesReader:
    """Build a bounded reader of the running llama.cpp server's own report.

    Args:
        settings: Inference settings whose base URL and key the adapter uses.

    Returns:
        A callable returning the `/props` object, or None when the server
        cannot answer. The key is sent as a header and never returned.
    """

    root = re.sub(r"/v1/?$", "", settings.llm_base_url.rstrip("/"))

    def read() -> Mapping[str, object] | None:
        """Read `/props` once, reporting only a stable reason on failure."""

        try:
            response = httpx.get(
                f"{root}/props",
                params={"model": settings.llm_model},
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                timeout=PROPERTIES_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            _LOGGER.warning(
                "tracing.identity.properties_unavailable",
                reason_code="endpoint_unreachable",
                exception_type=type(error).__name__,
            )
            return None
        if response.status_code >= 400:
            _LOGGER.warning(
                "tracing.identity.properties_unavailable",
                reason_code="endpoint_rejected",
                status_code=response.status_code,
            )
            return None
        try:
            return as_object_mapping(response.json())
        except (TypeError, ValueError):
            _LOGGER.warning(
                "tracing.identity.properties_unavailable",
                reason_code="invalid_properties",
            )
            return None

    return read


class ModelIdentityResolver:
    """Resolve and briefly remember the identity of the serving artifact."""

    def __init__(
        self,
        *,
        settings: Settings,
        properties: PropertiesReader,
        digests: ArtifactDigests,
        server_binary: str | None,
        configured_context_size: int | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the sources of identity.

        Args:
            settings: Inference settings the adapter uses.
            properties: Reader of the server's own report.
            digests: Digest measurement with revision caching.
            server_binary: Configured server executable name or path.
            configured_context_size: Context size from configuration.
            clock: Monotonic clock deciding when to re-read the report.
        """

        self._settings = settings
        self._properties = properties
        self._digests = digests
        self._server_binary = server_binary
        self._configured_context_size = configured_context_size
        self._clock = clock
        self._cached: dict[str, object] | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> ModelIdentityResolver:
        """Build a resolver from the process inference configuration.

        Returns:
            A resolver reading the configured server with a persistent cache.
        """

        settings = Settings.from_env()
        raw_context = os.getenv("LLAMA_CTX_SIZE", "").strip()
        return cls(
            settings=settings,
            properties=llama_properties_reader(settings),
            digests=ArtifactDigests(default_digest_cache_file()),
            server_binary=os.getenv("LLAMA_SERVER_BIN", "").strip() or None,
            configured_context_size=int(raw_context) if raw_context.isdigit() else None,
        )

    def __call__(self) -> dict[str, object]:
        """Return the current identity, re-reading the server when stale.

        Returns:
            The capability-report model fields; `metadata_complete` is false
            whenever the server could not be read or an artifact could not be
            measured. Sampling parameters travel with each request instead,
            because they belong to the chat model that sent it.
        """

        with self._lock:
            now = self._clock()
            if self._cached is not None and now < self._expires_at:
                return self._cached
            properties = self._properties()
            if properties is None:
                identity = self._unavailable()
                self._expires_at = now + PROPERTIES_RETRY_SECONDS
            else:
                identity = self._resolve(properties)
                self._expires_at = now + PROPERTIES_REFRESH_SECONDS
            self._cached = identity
            return identity

    def _resolve(self, properties: Mapping[str, object]) -> dict[str, object]:
        """Assemble identity from a server report and measured digests."""

        server_model = properties.get("model_path")
        model_path = Path(str(server_model)) if server_model else None
        binary = resolve_server_binary(self._server_binary)
        build = str(properties.get("build_info") or "").strip() or None
        metadata = model_metadata_from_properties(
            model_alias=self._settings.llm_model,
            properties=properties,
            filename=None if model_path is None else model_path.name,
            gguf_sha256=None
            if model_path is None
            else self._digests.digest(model_path),
            vision_projector_sha256=None,
            server_version=build,
            server_binary_sha256=None
            if binary is None
            else self._digests.digest(binary),
            configured_context_size=self._configured_context_size,
            vision_required=False,
        )
        return metadata.model_dump(mode="json")

    def _unavailable(self) -> dict[str, object]:
        """Describe the configured model when the server cannot be read."""

        return {"model_alias": self._settings.llm_model, "metadata_complete": False}


__all__ = [
    "ArtifactDigests",
    "ModelIdentityResolver",
    "PropertiesReader",
    "default_digest_cache_file",
    "llama_properties_reader",
]
