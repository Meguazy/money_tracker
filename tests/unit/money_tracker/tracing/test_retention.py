"""Tests for planning, exporting, and deleting traces in the local store."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from money_tracker.tracing.cli import _parser, _run
from money_tracker.tracing.retention import (
    ExportRefusedError,
    TraceStore,
    TraceStoreError,
)
from money_tracker.tracing.settings import TracingSettings

_CUTOFF = datetime(2026, 8, 18, 12, tzinfo=UTC)
_SETTINGS = TracingSettings(
    enabled=True,
    base_url="http://127.0.0.1:3000",
    public_key="pk-lf-test",
    secret_key="sk-lf-test-secret-value",
    environment="development",
)
_REPOSITORY = Path(__file__).resolve().parents[4]


def _trace(number: int) -> str:
    """Return a stable 32-character trace identifier."""

    return f"{number:032x}"


@dataclass
class InMemoryObservationApi:
    """The Langfuse observations and trace-deletion contract, held in memory.

    Deletion is applied only after `deletion_lag` further reads, as Langfuse
    applies it asynchronously. Pages hold at most `page_size` observations and
    are continued by an opaque cursor, as the public API does.
    """

    observations: list[dict[str, Any]] = field(default_factory=list)
    page_size: int = 2
    deletion_lag: int = 2
    pending_deletions: dict[str, int] = field(default_factory=dict)
    deleted_requests: list[str] = field(default_factory=list)
    fail_reads_after: int | None = None
    reads: int = 0

    def add(
        self,
        trace: int,
        start: datetime,
        *,
        environment: str = "development",
        text: str = "contenuto",
    ) -> None:
        """Store one observation of a trace."""

        self.observations.append(
            {
                "id": f"obs-{len(self.observations)}",
                "traceId": _trace(trace),
                "startTime": start.isoformat().replace("+00:00", "Z"),
                "environment": environment,
                "input": text,
            }
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one public API request."""

        if request.method == "DELETE":
            trace_id = request.url.path.rsplit("/", 1)[-1]
            self.deleted_requests.append(trace_id)
            self.pending_deletions[trace_id] = self.deletion_lag
            return httpx.Response(200, json={"message": "deletion queued"})
        self.reads += 1
        if self.fail_reads_after is not None and self.reads > self.fail_reads_after:
            return httpx.Response(503, json={"message": "unavailable"})
        self._apply_due_deletions()
        params = request.url.params
        matching = [
            item
            for item in self.observations
            if (not params.get("traceId") or item["traceId"] == params["traceId"])
            and (
                not params.get("environment")
                or item["environment"] == params["environment"]
            )
            and (
                not params.get("toStartTime")
                or _parse(item["startTime"]) < _parse(params["toStartTime"])
            )
        ]
        offset = (
            int(base64.b64decode(params["cursor"]).decode())
            if params.get("cursor")
            else 0
        )
        limit = min(int(params.get("limit", 50)), self.page_size)
        page = matching[offset : offset + limit]
        meta: dict[str, object] = {}
        if offset + limit < len(matching):
            meta["cursor"] = base64.b64encode(str(offset + limit).encode()).decode()
        return httpx.Response(200, json={"data": page, "meta": meta})

    def _apply_due_deletions(self) -> None:
        """Remove traces whose asynchronous deletion has come due."""

        for trace_id in list(self.pending_deletions):
            self.pending_deletions[trace_id] -= 1
            if self.pending_deletions[trace_id] < 0:
                self.observations = [
                    item for item in self.observations if item["traceId"] != trace_id
                ]
                del self.pending_deletions[trace_id]


def _parse(value: str) -> datetime:
    """Parse an API timestamp."""

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class _Clock:
    """A monotonic clock advanced by the store's own waits."""

    now: float = 0.0

    def __call__(self) -> float:
        """Return the current reading."""

        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance instead of waiting."""

        self.now += max(seconds, 0.001)


def _store(api: InMemoryObservationApi, clock: _Clock | None = None) -> TraceStore:
    """Bind the production store to the in-memory API."""

    clock = clock or _Clock()
    return TraceStore(
        _SETTINGS,
        client=httpx.Client(transport=httpx.MockTransport(api.handle)),
        sleep=clock.sleep,
        monotonic=clock,
    )


def _history() -> InMemoryObservationApi:
    """Two old traces, one crossing the cutoff, one new, one elsewhere."""

    api = InMemoryObservationApi()
    day = timedelta(days=1)
    api.add(1, _CUTOFF - 10 * day)
    api.add(1, _CUTOFF - 10 * day + timedelta(minutes=1))
    api.add(1, _CUTOFF - 10 * day + timedelta(minutes=2))
    api.add(2, _CUTOFF - 3 * day)
    api.add(3, _CUTOFF - 2 * day)
    api.add(3, _CUTOFF + timedelta(hours=1))
    api.add(4, _CUTOFF + day)
    api.add(5, _CUTOFF - 20 * day, environment="evaluation")
    return api


def test_a_plan_selects_only_traces_that_ended_before_the_cutoff() -> None:
    """Keep a trace whose retry or delivery crossed the cutoff whole."""

    plan = _store(_history()).plan(environment="development", cutoff=_CUTOFF)

    assert [item.trace_id for item in plan.eligible] == [_trace(1), _trace(2)]
    assert plan.eligible[0].observations == 3
    assert [item.trace_id for item in plan.spanning] == [_trace(3)]


def test_a_plan_follows_every_page_of_the_store() -> None:
    """Find traces beyond the first page of observations."""

    api = InMemoryObservationApi(page_size=1)
    for number in range(1, 8):
        api.add(number, _CUTOFF - timedelta(days=number))

    plan = _store(api).plan(environment="development", cutoff=_CUTOFF)

    assert len(plan.eligible) == 7


def test_a_naive_cutoff_is_refused() -> None:
    """Never guess which clock a cutoff was meant in."""

    with pytest.raises(ValueError):
        _store(_history()).plan(environment="development", cutoff=datetime(2026, 8, 1))


def _private_directory(tmp_path: Path) -> Path:
    """Create an owner-only directory for an export."""

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return directory


def test_an_export_writes_every_observation_to_a_new_owner_only_file(
    tmp_path: Path,
) -> None:
    """Keep content, write each trace as one line, and report the digest."""

    destination = _private_directory(tmp_path) / "traces.jsonl"

    result = _store(_history()).export(
        [_trace(1), _trace(2)], destination, forbidden_root=_REPOSITORY
    )

    lines = destination.read_bytes().splitlines()
    assert result.traces == 2
    assert result.observations == 4
    assert result.byte_count == destination.stat().st_size
    assert (
        result.sha256
        == f"sha256:{hashlib.sha256(destination.read_bytes()).hexdigest()}"
    )
    assert [json.loads(line)["traceId"] for line in lines] == [_trace(1), _trace(2)]
    assert json.loads(lines[0])["observations"][0]["input"] == "contenuto"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("destination", "reason"),
    [
        (lambda tmp: Path("relative/traces.jsonl"), "export_path_not_absolute"),
        (lambda tmp: _REPOSITORY / "out" / "traces.jsonl", "export_inside_repository"),
        (lambda tmp: tmp / "absent" / "traces.jsonl", "export_parent_missing"),
    ],
)
def test_an_unsafe_export_destination_is_refused(
    tmp_path: Path, destination: Any, reason: str
) -> None:
    """Refuse paths that could be committed, guessed, or not exist."""

    with pytest.raises(ExportRefusedError) as refused:
        _store(_history()).export(
            [_trace(1)], destination(tmp_path), forbidden_root=_REPOSITORY
        )

    assert refused.value.reason_code == reason


def test_an_export_never_overwrites_or_uses_a_shared_directory(
    tmp_path: Path,
) -> None:
    """Refuse an existing file and a group-writable parent."""

    private = _private_directory(tmp_path)
    existing = private / "traces.jsonl"
    existing.write_text("earlier export", encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o770)

    with pytest.raises(ExportRefusedError, match="export_path_exists"):
        _store(_history()).export([_trace(1)], existing, forbidden_root=_REPOSITORY)
    with pytest.raises(ExportRefusedError, match="export_parent_not_private"):
        _store(_history()).export(
            [_trace(1)], shared / "traces.jsonl", forbidden_root=_REPOSITORY
        )
    assert existing.read_text(encoding="utf-8") == "earlier export"


def test_a_failed_export_leaves_no_partial_file(tmp_path: Path) -> None:
    """Remove what was written when the store fails mid-export."""

    api = _history()
    api.fail_reads_after = 1
    destination = _private_directory(tmp_path) / "traces.jsonl"

    with pytest.raises(TraceStoreError) as failed:
        _store(api).export(
            [_trace(1), _trace(2)], destination, forbidden_root=_REPOSITORY
        )

    assert failed.value.reason_code == "store_failed"
    assert not destination.exists()


def test_a_deletion_is_verified_by_reading_until_the_store_applies_it() -> None:
    """Report a trace gone only once re-reading proves it."""

    api = _history()

    outcome = _store(api).delete([_trace(1), _trace(2)], verify_seconds=60)

    assert outcome.verified_absent == (_trace(1), _trace(2))
    assert outcome.remaining == ()
    assert {item["traceId"] for item in api.observations} == {
        _trace(3),
        _trace(4),
        _trace(5),
    }


def test_a_deletion_not_applied_within_the_bound_is_reported_remaining() -> None:
    """Never claim a deletion the store has not applied."""

    api = _history()
    api.deletion_lag = 10_000

    outcome = _store(api).delete([_trace(1)], verify_seconds=12)

    assert outcome.verified_absent == ()
    assert outcome.remaining == (_trace(1),)


def test_an_unreachable_store_is_a_transient_failure() -> None:
    """Name the reason without the endpoint URL."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to http://127.0.0.1:3000")

    store = TraceStore(
        _SETTINGS, client=httpx.Client(transport=httpx.MockTransport(refuse))
    )

    with pytest.raises(TraceStoreError) as failed:
        store.plan(environment="development", cutoff=_CUTOFF)

    assert failed.value.reason_code == "store_unreachable"
    assert failed.value.transient is True
    assert "127.0.0.1" not in str(failed.value)


def test_delete_without_confirmation_deletes_nothing() -> None:
    """Make the destructive command a dry run unless confirmed."""

    api = _history()
    args = _parser().parse_args(
        ["delete", "--environment", "development", "--before", _CUTOFF.isoformat()]
    )

    facts, code = _run(args, _store(api))

    assert code == 0
    assert facts["dry_run"] is True
    assert facts["selected_traces"] == 2
    assert facts["spanning_traces_kept"] == 1
    assert api.deleted_requests == []


def test_confirmed_deletion_by_identifier_reports_missing_traces() -> None:
    """Delete named traces, and say which named ones did not exist."""

    api = _history()
    args = _parser().parse_args(
        [
            "delete",
            "--trace-id",
            _trace(4),
            "--trace-id",
            _trace(9),
            "--confirm",
            "--verify-seconds",
            "60",
        ]
    )

    facts, code = _run(args, _store(api))

    assert code == 0
    assert facts["selected_by"] == "trace_id"
    assert facts["missing_traces"] == [_trace(9)]
    assert facts["deleted_verified"] == [_trace(4)]
    assert api.deleted_requests == [_trace(4)]


def test_the_default_age_is_the_owner_retention() -> None:
    """Select traces older than 30 days when no age is given."""

    args = _parser().parse_args(["plan"])
    api = InMemoryObservationApi()
    now = datetime.now(UTC)
    api.add(1, now - timedelta(days=31))
    api.add(2, now - timedelta(days=29))

    facts, _code = _run(args, _store(api))

    assert [item["trace_id"] for item in facts["traces"]] == [_trace(1)]  # type: ignore[index]


@pytest.mark.parametrize(
    "argv",
    [
        ["delete", "--trace-id", "not-a-trace"],
        ["plan", "--older-than-days", "0"],
        ["plan", "--before", "2026-08-18T12:00:00"],
        ["plan", "--older-than-days", "3", "--before", "2026-08-18T12:00:00+00:00"],
    ],
)
def test_ambiguous_or_malformed_selections_are_refused(argv: list[str]) -> None:
    """Refuse selections that could delete more than was meant."""

    with pytest.raises(SystemExit):
        _parser().parse_args(argv)
