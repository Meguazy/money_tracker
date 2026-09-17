"""Retention, export, and deletion of traces in the local store.

Langfuse 4.26.0 OSS has no project retention setting: the `data-retention`
entitlement belongs to the self-hosted Enterprise plan. The owner decided on a
30-day retention with an optional private export first, run by hand until
Step 7 schedules it, so the procedure is implemented here over the public API
the store does offer:

- traces are discovered from observations in one environment that started
  before the cutoff;
- a trace is eligible only when its most recent observation also started before
  the cutoff, so a message whose retry or delivery crossed the cutoff is kept
  whole rather than cut in half;
- an export writes every observation of the eligible traces, content included,
  to a new owner-only file outside the repository;
- deletion requests `DELETE /api/public/traces/{traceId}` per trace, which
  Langfuse applies asynchronously, and then re-reads each trace until it is
  gone or the verification bound passes, reporting what remains.

Nothing here imports the tracing SDK. Failures carry stable reason codes and
never an endpoint URL, a key, or observation content.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import httpx

from money_tracker.structured_logging import get_logger
from money_tracker.tracing.settings import TracingSettings

_LOGGER = get_logger(__name__)

PAGE_SIZE: Final = 500
"""Observations requested per page of the public API."""

EXPORT_FIELDS: Final = "core,basic,time,io,metadata,model,usage,metrics,prompt"
"""Observation field groups an export keeps: everything the store returns."""

REQUEST_TIMEOUT_SECONDS: Final = 30.0
"""Upper bound on one public API request."""


class TraceStoreError(RuntimeError):
    """The store refused, failed, or could not be reached.

    Attributes:
        reason_code: Stable machine-readable reason.
        transient: Whether retrying later could succeed.
    """

    def __init__(self, reason_code: str, *, transient: bool) -> None:
        """Bind a reason code without any request detail.

        Args:
            reason_code: Stable machine-readable reason.
            transient: Whether retrying later could succeed.
        """

        super().__init__(reason_code)
        self.reason_code = reason_code
        self.transient = transient


class ExportRefusedError(ValueError):
    """An export destination is unsafe for owner content.

    Attributes:
        reason_code: Stable machine-readable reason.
    """

    def __init__(self, reason_code: str) -> None:
        """Bind a reason code that names no path.

        Args:
            reason_code: Stable machine-readable reason.
        """

        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class TraceSummary:
    """What the store holds for one trace, without its content.

    Attributes:
        trace_id: Trace identifier.
        observations: Number of observations the trace holds.
        first_start: Start of its earliest observation.
        last_start: Start of its most recent observation.
    """

    trace_id: str
    observations: int
    first_start: datetime
    last_start: datetime


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """The traces a retention run would export or delete.

    Attributes:
        environment: Environment the plan covers.
        cutoff: Instant every eligible trace ended before.
        eligible: Traces whose most recent observation precedes the cutoff.
        spanning: Traces kept whole because they continue past the cutoff.
    """

    environment: str
    cutoff: datetime
    eligible: tuple[TraceSummary, ...]
    spanning: tuple[TraceSummary, ...]


@dataclass(frozen=True, slots=True)
class ExportResult:
    """A completed private export.

    Attributes:
        traces: Traces written.
        observations: Observations written.
        byte_count: Size of the file written.
        sha256: Digest of the file written.
    """

    traces: int
    observations: int
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DeletionResult:
    """The outcome of a verified deletion.

    Attributes:
        requested: Traces whose deletion was requested.
        verified_absent: Traces confirmed gone by re-reading.
        remaining: Traces still readable when the verification bound passed.
    """

    requested: tuple[str, ...]
    verified_absent: tuple[str, ...]
    remaining: tuple[str, ...]


class TraceStore:
    """Plan, export, and delete traces through the Langfuse public API."""

    def __init__(
        self,
        settings: TracingSettings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the store's validated loopback settings.

        Args:
            settings: Settings from `TracingSettings.for_command`.
            client: HTTP client to use; one is created when omitted.
            sleep: Waits between verification reads.
            monotonic: Clock bounding verification.
        """

        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._base_url = settings.base_url
        self._auth = (settings.public_key, settings.secret_key)
        self._sleep = sleep
        self._monotonic = monotonic

    def plan(self, *, environment: str, cutoff: datetime) -> RetentionPlan:
        """Find the traces of one environment that ended before a cutoff.

        Args:
            environment: Langfuse environment to cover.
            cutoff: Aware instant a trace must have ended before.

        Returns:
            Eligible traces and the traces kept because they span the cutoff,
            both oldest first.

        Raises:
            TraceStoreError: If the store cannot be read.
            ValueError: If the cutoff is naive.
        """

        if cutoff.tzinfo is None:
            raise ValueError("retention cutoff must be timezone-aware")
        candidates: list[str] = []
        for observation in self._observations(
            {
                "environment": environment,
                "toStartTime": cutoff.isoformat(),
                "fields": "core",
            }
        ):
            trace_id = str(observation["traceId"])
            if trace_id not in candidates:
                candidates.append(trace_id)
        summaries = self.summarize(candidates)
        eligible = tuple(item for item in summaries if item.last_start < cutoff)
        spanning = tuple(item for item in summaries if item.last_start >= cutoff)
        _LOGGER.info(
            "traces.plan.completed",
            environment=environment,
            eligible=len(eligible),
            spanning=len(spanning),
        )
        return RetentionPlan(
            environment=environment,
            cutoff=cutoff,
            eligible=eligible,
            spanning=spanning,
        )

    def summarize(self, trace_ids: Sequence[str]) -> tuple[TraceSummary, ...]:
        """Count and date the observations of named traces.

        Args:
            trace_ids: Traces to describe; absent ones are omitted.

        Returns:
            One summary per trace the store still holds, oldest first.

        Raises:
            TraceStoreError: If the store cannot be read.
        """

        summaries = []
        for trace_id in trace_ids:
            starts = [
                _instant(observation["startTime"])
                for observation in self._observations(
                    {"traceId": trace_id, "fields": "core"}
                )
            ]
            if starts:
                summaries.append(
                    TraceSummary(
                        trace_id=trace_id,
                        observations=len(starts),
                        first_start=min(starts),
                        last_start=max(starts),
                    )
                )
        return tuple(sorted(summaries, key=lambda item: item.first_start))

    def export(
        self,
        trace_ids: Sequence[str],
        destination: Path,
        *,
        forbidden_root: Path,
    ) -> ExportResult:
        """Write every observation of named traces to a new private file.

        Each line holds one trace: its identifier and all its observations with
        their complete input, output, and metadata. The file is owner content
        with the sensitivity of the ledger.

        Args:
            trace_ids: Traces to export.
            destination: Absolute path of a file that must not exist yet.
            forbidden_root: Directory the file must not be inside, normally the
                repository checkout, so an export cannot be committed.

        Returns:
            Counts, size, and digest of what was written.

        Raises:
            ExportRefusedError: If the destination is unsafe.
            TraceStoreError: If the store cannot be read; the partial file is
                removed.
        """

        _require_private_destination(destination, forbidden_root=forbidden_root)
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        observations = 0
        byte_count = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                for trace_id in trace_ids:
                    recorded = list(
                        self._observations(
                            {"traceId": trace_id, "fields": EXPORT_FIELDS}
                        )
                    )
                    line = (
                        json.dumps(
                            {"traceId": trace_id, "observations": recorded},
                            sort_keys=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                    handle.write(line)
                    digest.update(line)
                    observations += len(recorded)
                    byte_count += len(line)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        _LOGGER.info(
            "traces.export.completed",
            traces=len(trace_ids),
            observations=observations,
            byte_count=byte_count,
        )
        return ExportResult(
            traces=len(trace_ids),
            observations=observations,
            byte_count=byte_count,
            sha256=f"sha256:{digest.hexdigest()}",
        )

    def delete(
        self, trace_ids: Sequence[str], *, verify_seconds: float
    ) -> DeletionResult:
        """Request deletion of named traces and confirm it by re-reading.

        Args:
            trace_ids: Traces to delete.
            verify_seconds: How long to keep re-reading for asynchronous
                deletion to be applied.

        Returns:
            Which traces were confirmed absent and which remained.

        Raises:
            TraceStoreError: If a deletion request is refused or the store
                cannot be reached.
        """

        requested = tuple(dict.fromkeys(trace_ids))
        for trace_id in requested:
            self._request("DELETE", f"/api/public/traces/{trace_id}")
        _LOGGER.info("traces.delete.requested", traces=len(requested))
        pending = list(requested)
        deadline = self._monotonic() + verify_seconds
        while pending:
            pending = [
                trace_id
                for trace_id in pending
                if next(
                    iter(
                        self._observations(
                            {"traceId": trace_id, "fields": "core", "limit": 1},
                            paginate=False,
                        )
                    ),
                    None,
                )
                is not None
            ]
            if not pending or self._monotonic() >= deadline:
                break
            self._sleep(min(5.0, max(0.0, deadline - self._monotonic())))
        remaining = tuple(pending)
        verified = tuple(item for item in requested if item not in remaining)
        if remaining:
            _LOGGER.warning(
                "traces.delete.unverified",
                verified=len(verified),
                remaining=len(remaining),
            )
        else:
            _LOGGER.info("traces.delete.verified", verified=len(verified))
        return DeletionResult(
            requested=requested, verified_absent=verified, remaining=remaining
        )

    def close(self) -> None:
        """Release the HTTP client."""

        self._client.close()

    def _observations(
        self, params: Mapping[str, Any], *, paginate: bool = True
    ) -> Iterator[dict[str, Any]]:
        """Yield observations matching the parameters, following the cursor."""

        query: dict[str, Any] = {"limit": PAGE_SIZE, **params}
        while True:
            body = self._request("GET", "/api/public/v2/observations", params=query)
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise TraceStoreError("invalid_response", transient=True)
            yield from (item for item in data if isinstance(item, dict))
            meta = body.get("meta") if isinstance(body, dict) else None
            cursor = meta.get("cursor") if isinstance(meta, dict) else None
            if not paginate or not cursor or not data:
                return
            query = {**query, "cursor": cursor}

    def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        """Send one authenticated request and reduce failures to reason codes."""

        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                params=dict(params or {}),
                auth=self._auth,
            )
        except httpx.TimeoutException:
            raise TraceStoreError("store_timeout", transient=True) from None
        except httpx.HTTPError:
            raise TraceStoreError("store_unreachable", transient=True) from None
        if response.status_code in {401, 403}:
            raise TraceStoreError("store_rejected_credentials", transient=False)
        if response.status_code >= 500:
            raise TraceStoreError("store_failed", transient=True)
        if response.status_code >= 400:
            raise TraceStoreError(
                f"store_refused_{response.status_code}", transient=False
            )
        if method == "DELETE":
            return None
        try:
            return response.json()
        except ValueError:
            raise TraceStoreError("invalid_response", transient=True) from None


def _instant(value: object) -> datetime:
    """Parse an observation's start time as an aware instant."""

    if not isinstance(value, str):
        raise TraceStoreError("invalid_response", transient=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise TraceStoreError("invalid_response", transient=True) from None
    if parsed.tzinfo is None:
        raise TraceStoreError("invalid_response", transient=True)
    return parsed


def _require_private_destination(destination: Path, *, forbidden_root: Path) -> None:
    """Refuse an export path that could expose or commit owner content.

    Args:
        destination: Requested export file.
        forbidden_root: Directory the file must not be inside.

    Raises:
        ExportRefusedError: If the path is relative, exists, sits inside the
            forbidden root, or has a parent that is missing, not a directory,
            or writable by anyone but the owner.
    """

    if not destination.is_absolute():
        raise ExportRefusedError("export_path_not_absolute")
    if destination.exists() or destination.is_symlink():
        raise ExportRefusedError("export_path_exists")
    parent = destination.parent
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError:
        raise ExportRefusedError("export_parent_missing") from None
    if not resolved_parent.is_dir():
        raise ExportRefusedError("export_parent_not_directory")
    root = forbidden_root.resolve()
    if resolved_parent == root or root in resolved_parent.parents:
        raise ExportRefusedError("export_inside_repository")
    status = resolved_parent.stat()
    if status.st_uid != os.getuid() or status.st_mode & 0o022:
        raise ExportRefusedError("export_parent_not_private")


__all__ = [
    "DeletionResult",
    "ExportRefusedError",
    "ExportResult",
    "RetentionPlan",
    "TraceStore",
    "TraceStoreError",
    "TraceSummary",
]
