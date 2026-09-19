"""Operator command for trace retention, export, deletion, and latency profiling.

`money-tracker-traces` implements the owner's retention decision: runtime
traces are kept 30 days and may be exported privately before they are deleted.
Every subcommand is its own explicit request and works only against the
loopback store the tracing settings accept. Deletion is a dry run unless
`--confirm` is given, and a confirmed deletion reports only what re-reading the
store proved gone. `profile` reads observation names and times only and
reports latency distributions for a window.

Standard output is the command's JSON result: counts, instants, trace
identifiers, digests, and durations, never observation content or the export
path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from money_tracker.latency import render_summary
from money_tracker.structured_logging import configure_logging, get_logger
from money_tracker.tracing.profile import (
    ObservationProfile,
    TraceProfile,
    profile_timings,
)
from money_tracker.tracing.retention import (
    ExportRefusedError,
    RetentionPlan,
    TraceStore,
    TraceStoreError,
    TraceSummary,
)
from money_tracker.tracing.settings import (
    DEFAULT_ENVIRONMENT,
    LANGFUSE_ENVIRONMENT_ENV,
    TracingConfigurationError,
    TracingSettings,
)

_LOGGER = get_logger(__name__)

RETENTION_DAYS: Final = 30
"""Owner-decided retention of runtime traces (2026-09-17)."""

VERIFY_SECONDS: Final = 600.0
"""Default bound on waiting for Langfuse to apply a deletion."""

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    """Build the trace retention command parser."""

    parser = argparse.ArgumentParser(
        description="Plan, export, delete, and profile traces in the local Langfuse"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("plan", "list the traces a retention run would remove, changing nothing"),
        ("export", "write the selected traces to a new private JSONL file"),
        ("delete", "delete the selected traces and verify it; dry run by default"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument(
            "--environment",
            default=os.getenv(LANGFUSE_ENVIRONMENT_ENV, "").strip()
            or DEFAULT_ENVIRONMENT,
            help="Langfuse environment to cover (default: the tracing environment)",
        )
        selection = command.add_mutually_exclusive_group()
        selection.add_argument(
            "--older-than-days",
            type=_positive_days,
            help=(
                "select traces that ended this many days ago "
                f"(default: {RETENTION_DAYS})"
            ),
        )
        selection.add_argument(
            "--before",
            type=_aware_instant,
            help="select traces that ended before this aware ISO-8601 instant",
        )
        if name != "plan":
            selection.add_argument(
                "--trace-id",
                action="append",
                type=_trace_id,
                help="select one trace by identifier; repeat for several",
            )
    subparsers.choices["export"].add_argument(
        "--output",
        type=Path,
        required=True,
        help="absolute path of a new file in a private directory outside the checkout",
    )
    profile = subparsers.add_parser(
        "profile",
        help="report latency distributions of a window, reading no content",
    )
    profile.add_argument(
        "--environment",
        default=os.getenv(LANGFUSE_ENVIRONMENT_ENV, "").strip() or DEFAULT_ENVIRONMENT,
        help="Langfuse environment to profile (default: the tracing environment)",
    )
    profile.add_argument(
        "--since",
        type=_aware_instant,
        required=True,
        help="profile observations that started at or after this aware instant",
    )
    profile.add_argument(
        "--until",
        type=_aware_instant,
        help="profile observations that started before this aware instant",
    )
    delete = subparsers.choices["delete"]
    delete.add_argument(
        "--confirm",
        action="store_true",
        help="actually delete; without it the command only reports its plan",
    )
    delete.add_argument(
        "--verify-seconds",
        type=float,
        default=VERIFY_SECONDS,
        help="how long to re-read the store for the asynchronous deletion",
    )
    return parser


def _positive_days(value: str) -> int:
    """Parse a whole positive number of days."""

    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("--older-than-days must be a positive integer")
    return int(value)


def _aware_instant(value: str) -> datetime:
    """Parse an aware ISO-8601 instant."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--before must be an aware ISO-8601 instant"
        ) from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--before must be an aware ISO-8601 instant")
    return parsed


def _trace_id(value: str) -> str:
    """Accept a 32-character lowercase hexadecimal trace identifier."""

    if len(value) != 32 or any(char not in "0123456789abcdef" for char in value):
        raise argparse.ArgumentTypeError("--trace-id must be 32 lowercase hex digits")
    return value


def _cutoff(args: argparse.Namespace) -> datetime:
    """Resolve the selected age into an aware cutoff instant."""

    if args.before is not None:
        return args.before  # type: ignore[no-any-return]
    days = args.older_than_days or RETENTION_DAYS
    return datetime.now(UTC) - timedelta(days=days)


def _summary(item: TraceSummary) -> dict[str, object]:
    """Render one trace summary without content."""

    return {
        "trace_id": item.trace_id,
        "observations": item.observations,
        "first_start": item.first_start.isoformat(),
        "last_start": item.last_start.isoformat(),
    }


def _plan_facts(plan: RetentionPlan) -> dict[str, object]:
    """Render a retention plan as the command's JSON facts."""

    return {
        "environment": plan.environment,
        "cutoff": plan.cutoff.isoformat(),
        "selected_traces": len(plan.eligible),
        "selected_observations": sum(item.observations for item in plan.eligible),
        "spanning_traces_kept": len(plan.spanning),
        "traces": [_summary(item) for item in plan.eligible],
    }


def _selection(
    store: TraceStore, args: argparse.Namespace
) -> tuple[list[str], dict[str, object]]:
    """Resolve the traces a command acts on and describe the selection."""

    trace_ids = getattr(args, "trace_id", None)
    if trace_ids:
        present = store.summarize(trace_ids)
        found = {item.trace_id for item in present}
        return [item.trace_id for item in present], {
            "selected_by": "trace_id",
            "selected_traces": len(present),
            "missing_traces": [item for item in trace_ids if item not in found],
            "traces": [_summary(item) for item in present],
        }
    plan = store.plan(environment=args.environment, cutoff=_cutoff(args))
    return [item.trace_id for item in plan.eligible], {
        "selected_by": "age",
        **_plan_facts(plan),
    }


def _profile_facts(profile: TraceProfile) -> dict[str, object]:
    """Render a latency profile as the command's JSON facts."""

    updates = profile.updates
    return {
        "environment": profile.environment,
        "since": profile.since.isoformat(),
        "until": None if profile.until is None else profile.until.isoformat(),
        "observation_count": profile.observation_count,
        "telegram_updates": {
            "complete": updates.complete,
            "incomplete": updates.incomplete,
            "retried": updates.retried,
            "wait_ms": render_summary(updates.wait),
            "processing_ms": render_summary(updates.processing),
            "delivery_ms": render_summary(updates.delivery),
            "total_ms": render_summary(updates.total),
        },
        "observations": [
            {"kind": item.kind, "name": item.name, **_rendered(item)}
            for item in profile.observations
        ],
    }


def _rendered(item: ObservationProfile) -> dict[str, int]:
    """Render one observation distribution, which always has samples."""

    rendered = render_summary(item.durations)
    assert rendered is not None
    return rendered


def _run(args: argparse.Namespace, store: TraceStore) -> tuple[dict[str, object], int]:
    """Execute one subcommand and return its JSON result and exit code."""

    if args.command == "profile":
        timings = store.timings(
            environment=args.environment, since=args.since, until=args.until
        )
        return _profile_facts(
            profile_timings(
                timings,
                environment=args.environment,
                since=args.since,
                until=args.until,
            )
        ), 0
    selected, facts = _selection(store, args)
    if args.command == "plan":
        return facts, 0
    if args.command == "export":
        result = store.export(selected, args.output, forbidden_root=_REPOSITORY_ROOT)
        return {
            **facts,
            "exported_traces": result.traces,
            "exported_observations": result.observations,
            "byte_count": result.byte_count,
            "sha256": result.sha256,
        }, 0
    if not args.confirm:
        return {**facts, "dry_run": True}, 0
    outcome = store.delete(selected, verify_seconds=args.verify_seconds)
    return {
        **facts,
        "dry_run": False,
        "deleted_verified": list(outcome.verified_absent),
        "remaining": list(outcome.remaining),
    }, (0 if not outcome.remaining else 1)


def main(argv: Sequence[str] | None = None) -> None:
    """Run one trace retention operation and print its JSON result.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: With 1 when configuration, an export destination, or a
            deletion's verification fails, and 2 when the store is
            unreachable.
    """

    configure_logging()
    args = _parser().parse_args(argv)
    _LOGGER.info("traces.command.started", command=args.command)
    try:
        settings = TracingSettings.for_command(environment=args.environment)
    except TracingConfigurationError as error:
        _LOGGER.error("tracing.configuration.rejected", reason_code=error.reason_code)
        print(f"Trace command refused: {error.reason_code}", file=sys.stderr)
        raise SystemExit(1) from None
    store = TraceStore(settings)
    try:
        facts, code = _run(args, store)
    except ExportRefusedError as error:
        _LOGGER.error("traces.export.refused", reason_code=error.reason_code)
        print(f"Trace export refused: {error.reason_code}", file=sys.stderr)
        raise SystemExit(1) from None
    except TraceStoreError as error:
        _LOGGER.error(
            "traces.store.failed",
            reason_code=error.reason_code,
            transient=error.transient,
        )
        print(f"Trace store failed: {error.reason_code}", file=sys.stderr)
        raise SystemExit(2 if error.transient else 1) from None
    finally:
        store.close()
    print(json.dumps({"command": args.command, **facts}, sort_keys=True))
    if code:
        raise SystemExit(code)


__all__ = ["RETENTION_DAYS", "main"]
