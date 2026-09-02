"""Minimal local administration entry points for active development steps.

These commands exist so an owner can exercise implemented components by hand
before a channel or dashboard exists. They are development and bootstrap
entry points, not the product surface, and each one is deliberately narrower
than the workflow that will eventually drive the same component.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from langchain_core.exceptions import ModelConnectionError

from money_tracker.application import (
    CategoryProposalService,
    FinanceService,
    ReceiptMediaError,
    ReceiptMetadataService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import ReceiptMetadata
from money_tracker.domain.errors import DomainError
from money_tracker.media import LocalReceiptMediaStore, ReceiptArchiveSettings
from money_tracker.media.local import MAX_RECEIPT_BYTES
from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import (
    LocalStructuredOutputModel,
    build_expense_graph,
    build_router_graph,
)
from money_tracker.workflows import (
    CANDIDATE_CATEGORY_FIT_INSTRUCTIONS,
    CANDIDATE_EXTRACTION_INSTRUCTIONS,
    CANDIDATE_ROUTER_INSTRUCTIONS,
    ChannelMessage,
    ExpenseWorkflowState,
    RouterState,
    RoutingContext,
    RoutingDecision,
    WorkflowIntent,
    initial_expense_state,
    initial_router_state,
    routing_decision_from_state,
)


def _parser() -> argparse.ArgumentParser:
    """Build the local administration parser."""

    parser = argparse.ArgumentParser(description="Manage local Money Tracker data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    receipt = subparsers.add_parser(
        "receipt-store",
        help="validate, retain, and register one original receipt",
    )
    receipt.add_argument("--profile-id", required=True, type=UUID)
    receipt.add_argument("--file", required=True, type=Path)
    receipt.add_argument("--source-reference", required=True)
    receipt.add_argument("--original-filename")
    route = subparsers.add_parser(
        "route",
        help=(
            "send one message through the real root router and print only its "
            "routing decision; extraction and posting are not implemented yet"
        ),
    )
    route.add_argument("--text")
    route.add_argument(
        "--minimum-confidence",
        required=True,
        type=float,
        help=(
            "confidence bar to apply; required because no calibrated production "
            "value is promoted"
        ),
    )
    route.add_argument(
        "--with-image",
        action="store_true",
        help="treat the message as carrying image media",
    )
    route.add_argument(
        "--pending-review-id",
        action="append",
        type=UUID,
        default=[],
        metavar="UUID",
        help="a category review awaiting the owner; repeat for several",
    )
    route.add_argument(
        "--latest-movement-id",
        type=UUID,
        metavar="UUID",
        help="the movement a correction or undo would target",
    )
    route.add_argument(
        "--profile-id",
        type=UUID,
        metavar="UUID",
        help=(
            "run the expense or refund workflow for this profile when routing "
            "selects one, storing a draft; routing only when omitted"
        ),
    )
    return parser


def _read_private_input(path: Path) -> bytes:
    """Read a bounded regular input file without following a final symlink.

    Args:
        path: Owner-selected local receipt file.

    Returns:
        At most the configured receipt byte limit.

    Raises:
        ReceiptMediaError: If the path is unsafe, missing, or oversized.
    """

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            content = handle.read(MAX_RECEIPT_BYTES + 1)
    except OSError as error:
        raise ReceiptMediaError("receipt input must be a safe regular file") from error
    if len(content) > MAX_RECEIPT_BYTES:
        raise ReceiptMediaError("receipt input exceeds the byte limit")
    return content


def _receipt_store(args: argparse.Namespace) -> dict[str, object]:
    """Store and register one receipt after validating all cheap preconditions."""

    database_url = database_url_from_env()
    repository = PostgresFinanceRepository(database_url)
    if not repository.profile_exists(args.profile_id):
        raise SystemExit("active profile does not exist")
    source_reference = args.source_reference.strip()
    if not source_reference:
        raise SystemExit("--source-reference cannot be empty")
    original_filename = args.original_filename or args.file.name
    if (
        not original_filename.strip()
        or Path(original_filename).name != original_filename
    ):
        raise SystemExit("--original-filename must be a display name, not a path")

    content = _read_private_input(args.file)
    media = LocalReceiptMediaStore(ReceiptArchiveSettings.from_env().root).store(
        content
    )
    metadata = ReceiptMetadata(
        id=uuid4(),
        profile_id=args.profile_id,
        content_sha256=media.content_sha256,
        media_type=media.media_type,
        byte_size=media.byte_size,
        stored_at=datetime.now(UTC),
        source_reference=source_reference,
        original_filename=original_filename,
    )
    persisted = ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    ).register(
        metadata,
        actor="local-admin",
        reason="validated original stored through administration CLI",
    )
    return {
        "id": str(persisted.id),
        "content_sha256": persisted.content_sha256,
        "media_type": persisted.media_type,
        "byte_size": persisted.byte_size,
        "status": persisted.status.value,
    }


def _route(args: argparse.Namespace) -> dict[str, object]:
    """Route one message through the real router and return its decision.

    The trusted routing context is supplied by flags rather than read from the
    database, so the command exercises the router without requiring an active
    profile. That also makes the policy vetoes reachable by hand: without a
    pending review or a resolvable movement, a follow-up, correction, or undo
    is clarified rather than accepted.

    A deterministic route needs no inference call at all. Only free text
    reaches the model, so a running local endpoint is required for that case
    and for nothing else.

    Args:
        args: Parsed `route` arguments.

    Returns:
        Non-sensitive decision facts. The owner message is not echoed, because
        a routing decision deliberately carries no message content.

    Raises:
        SystemExit: If the message carries nothing routable, the confidence bar
            lies outside the unit interval, or the local endpoint is
            unreachable. An unreachable endpoint is a normal operational state
            for a development command, so it names the missing service rather
            than surfacing a transport traceback. It is never turned into a
            routing decision.
    """

    try:
        message = ChannelMessage(
            source_reference="local-admin:route",
            received_at=datetime.now(UTC),
            text=args.text,
            has_image=args.with_image,
        )
    except DomainError as error:
        raise SystemExit(f"nothing to route: {error}") from error
    if not 0.0 <= args.minimum_confidence <= 1.0:
        raise SystemExit("--minimum-confidence must lie between 0 and 1")

    graph = build_router_graph(
        model=LocalStructuredOutputModel(),
        instructions=CANDIDATE_ROUTER_INSTRUCTIONS,
        minimum_confidence=args.minimum_confidence,
    )
    initial = initial_router_state(
        message,
        context=RoutingContext(
            pending_category_review_ids=tuple(args.pending_review_id),
            latest_correctable_movement_id=args.latest_movement_id,
        ),
    )
    try:
        # The compiled graph returns its state as a plain mapping; the router's
        # own state contract names the keys it may contain.
        final = cast(RouterState, graph.invoke(initial))
    except ModelConnectionError as error:
        raise SystemExit(
            "free text needs the local inference server; start it with "
            "scripts/run_llama_server.sh (a deterministic route needs no model)"
        ) from error
    decision = routing_decision_from_state(final)
    if decision is None:
        raise SystemExit("router reached no decision")
    routed = _routing_facts(decision)
    if args.profile_id is None or decision.intent not in {
        WorkflowIntent.EXPENSE,
        WorkflowIntent.REFUND,
    }:
        return routed
    return routed | _run_movement_workflow(
        message, profile_id=args.profile_id, intent=decision.intent
    )


def _routing_facts(decision: RoutingDecision) -> dict[str, object]:
    """Render the safe facts of one routing decision."""

    return {
        "command": decision.command.value if decision.command else None,
        "confidence": decision.confidence,
        "intent": decision.intent.value,
        "pending_action_id": (
            str(decision.pending_action_id) if decision.pending_action_id else None
        ),
        "reason": decision.reason.value,
        "source": decision.source.value,
        "target_movement_id": (
            str(decision.target_movement_id) if decision.target_movement_id else None
        ),
    }


def _run_movement_workflow(
    message: ChannelMessage,
    *,
    profile_id: UUID,
    intent: WorkflowIntent,
) -> dict[str, object]:
    """Run the expense or refund workflow and return its safe outcome.

    The profile scope is supplied by the operator, which is what makes this a
    development command: a channel would derive it from owner pairing instead.
    Nothing is stored unless every part of the message resolves, and a stored
    movement is always a draft.

    Args:
        message: The inbound record routing accepted.
        profile_id: Trusted owner scope for repository reads and writes.
        intent: The movement route routing selected.

    Returns:
        Outcome facts, including the stored draft's identifier when one exists.

    Raises:
        SystemExit: If the profile does not exist or the local endpoint is
            unreachable.
    """

    repository = PostgresFinanceRepository(database_url_from_env())
    if not repository.profile_exists(profile_id):
        raise SystemExit("active profile does not exist")
    graph = build_expense_graph(
        model=LocalStructuredOutputModel(),
        configuration=repository,
        finance=FinanceService(repository, clock=SystemClock(), ids=UUID4Generator()),
        proposals=CategoryProposalService(
            repository, clock=SystemClock(), ids=UUID4Generator()
        ),
        ids=UUID4Generator(),
        extraction_instructions=CANDIDATE_EXTRACTION_INSTRUCTIONS,
        category_instructions=CANDIDATE_CATEGORY_FIT_INSTRUCTIONS,
    )
    try:
        final = cast(
            ExpenseWorkflowState,
            graph.invoke(
                initial_expense_state(
                    message, profile_id=profile_id, routed_intent=intent
                )
            ),
        )
    except ModelConnectionError as error:
        raise SystemExit(
            "the expense workflow needs the local inference server; start it "
            "with scripts/run_llama_server.sh"
        ) from error
    return {
        "clarification_reason": final.get("clarification_reason"),
        "expense_id": final.get("expense_id"),
        "outcome": final.get("outcome"),
        "proposal_id": final.get("proposal_id"),
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Run a local administration command and emit non-sensitive JSON.

    Args:
        argv: Optional arguments used instead of process arguments.
    """

    args = _parser().parse_args(argv)
    if args.command == "receipt-store":
        print(json.dumps(_receipt_store(args), sort_keys=True))
        return
    if args.command == "route":
        print(json.dumps(_route(args), sort_keys=True))
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
