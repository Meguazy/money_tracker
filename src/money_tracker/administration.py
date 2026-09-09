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
from uuid import UUID, uuid4

from langchain_core.exceptions import ModelConnectionError
from langgraph.checkpoint.base import BaseCheckpointSaver

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
    FinanceService,
    ReceiptMediaError,
    ReceiptMetadataService,
    ReportingService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    CategoryStatus,
    FundingClass,
    FundingSource,
    Profile,
    ReceiptMetadata,
    initial_taxonomy,
)
from money_tracker.domain.errors import DomainError
from money_tracker.domain.taxonomy_seed import TAXONOMY_VERSION
from money_tracker.media import LocalReceiptMediaStore, ReceiptArchiveSettings
from money_tracker.media.local import MAX_RECEIPT_BYTES
from money_tracker.persistence.database import (
    database_url_from_env,
    open_postgres_checkpointer,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import (
    LocalStructuredOutputModel,
    WorkflowDispatcher,
    build_router_graph,
    build_workflow_dispatcher,
)
from money_tracker.runtime.read_graph import DEFAULT_SELECTION
from money_tracker.workflows import (
    CATEGORY_FIT,
    DEFAULT_READ_SELECTION_POLICY,
    EXTRACTION,
    ROUTER,
    TOOL_SELECTION,
    CallbackAction,
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    PendingMovementAction,
    ReadSelectionPolicyVersion,
    RoutingContext,
    RoutingDecision,
    default_prompt,
    normalize_owner_text,
)
from money_tracker.workflows.candidates import (
    DEFAULT_CATEGORY_FIT_CANDIDATE,
    DEFAULT_EXTRACTION_CANDIDATE,
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
            "send one normalized message through the root router and its "
            "Step 3 specialist, when a profile is supplied"
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
        "--callback-action",
        choices=[item.value for item in CallbackAction],
        help="typed action selected by the owner",
    )
    route.add_argument(
        "--pending-action-id",
        type=UUID,
        metavar="UUID",
        help="pending action reference carried by a callback",
    )
    route.add_argument(
        "--reply-to-action-id",
        type=UUID,
        metavar="UUID",
        help="pending review reference addressed by a free-text reply",
    )
    route.add_argument(
        "--source-reference",
        help="stable inbound reference; generated for a one-shot call when omitted",
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
            "run the selected Step 3 specialist in this trusted profile; "
            "routing-only diagnostics when omitted"
        ),
    )
    route.add_argument(
        "--read-selection-policy",
        choices=[version.value for version in ReadSelectionPolicyVersion],
        default=DEFAULT_READ_SELECTION_POLICY.value,
        help=(
            "versioned read-selection policy "
            "(default: promoted explicit-finance-scope-v1)"
        ),
    )
    subparsers.add_parser(
        "profile-init",
        help="create the sole profile with the accepted taxonomy seed",
    )
    subparsers.add_parser(
        "profile-list",
        help="list profiles and their configured funding sources",
    )
    source = subparsers.add_parser(
        "funding-source-add",
        help="add one funding source to an existing profile",
    )
    source.add_argument("--profile-id", required=True, type=UUID)
    source.add_argument("--name", required=True)
    source.add_argument(
        "--funding-class",
        required=True,
        choices=[item.value for item in FundingClass],
    )
    source.add_argument(
        "--balance-mode",
        default=BalanceMode.UNTRACKED.value,
        choices=[item.value for item in BalanceMode],
        help="ledger only for a resource whose balance this app should track",
    )
    source.add_argument(
        "--default",
        action="store_true",
        dest="is_default",
        help="resolve an omitted payment method to this source",
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
    """Dispatch one normalized message through the real Step 3 entry point.

    The operator supplies the same trusted correlations a future durable worker
    will derive from repositories.  Without a profile the command remains a
    routing-only diagnostic and opens no finance specialist.

    Args:
        args: Parsed ``route`` arguments.

    Returns:
        Non-sensitive routing and specialist outcome facts.

    Raises:
        SystemExit: If the work unit is invalid, profile scope is unknown, the
            confidence bar is invalid, or local inference is unavailable.
    """

    try:
        message = ChannelMessage(
            source_reference=(args.source_reference or f"local-admin:route:{uuid4()}"),
            received_at=datetime.now(UTC),
            text=args.text,
            callback_action=(
                None
                if args.callback_action is None
                else CallbackAction(args.callback_action)
            ),
            pending_action_id=args.pending_action_id,
            reply_to_action_id=args.reply_to_action_id,
            has_image=args.with_image,
        )
    except DomainError as error:
        raise SystemExit(f"nothing to route: {error}") from error
    if not 0.0 <= args.minimum_confidence <= 1.0:
        raise SystemExit("--minimum-confidence must lie between 0 and 1")

    ids = UUID4Generator()
    model = LocalStructuredOutputModel()
    context = DispatchContext(
        profile_id=args.profile_id,
        routing=RoutingContext(
            pending_category_review_ids=tuple(args.pending_review_id),
            latest_correctable_movement_id=args.latest_movement_id,
        ),
        movement_actions=(
            ()
            if args.pending_action_id is None or args.latest_movement_id is None
            else (
                PendingMovementAction(args.pending_action_id, args.latest_movement_id),
            )
        ),
    )

    if args.profile_id is None:
        dispatcher = WorkflowDispatcher.routing_only(
            build_router_graph(
                model=model,
                instructions=default_prompt(ROUTER).text,
                minimum_confidence=args.minimum_confidence,
            )
        )
        try:
            return _dispatch_facts(dispatcher.dispatch(message, context=context))
        except ModelConnectionError as error:
            raise SystemExit(
                "this work unit needs the local inference server; start it with "
                "scripts/run_llama_server.sh (deterministic routes need no model)"
            ) from error

    database_url = database_url_from_env()
    repository = PostgresFinanceRepository(database_url)
    if not repository.profile_exists(args.profile_id):
        raise SystemExit("active profile does not exist")

    def run(
        checkpointer: BaseCheckpointSaver[str] | None = None,
    ) -> DispatchOutcome:
        """Assemble against the selected checkpoint lifetime and dispatch once."""

        funding_source_names = tuple(
            source.name for source in repository.get_funding_sources(args.profile_id)
        )
        category_names = tuple(
            category.name
            for category in repository.get_taxonomy(args.profile_id).categories
            if category.status is CategoryStatus.ACTIVE
        )
        dispatcher = build_workflow_dispatcher(
            model=model,
            configuration=repository,
            finance=FinanceService(repository, clock=SystemClock(), ids=ids),
            proposals=CategoryProposalService(repository, clock=SystemClock(), ids=ids),
            review=CategoryReviewService(
                repository,
                repository,
                clock=SystemClock(),
                ids=ids,
            ),
            reporting=ReportingService(repository),
            ids=ids,
            router_instructions=default_prompt(ROUTER).text,
            minimum_confidence=args.minimum_confidence,
            extraction_instructions=default_prompt(EXTRACTION).text,
            category_instructions=default_prompt(CATEGORY_FIT).text,
            selection_instructions=default_prompt(TOOL_SELECTION).text,
            extraction_candidate=DEFAULT_EXTRACTION_CANDIDATE,
            category_candidate=DEFAULT_CATEGORY_FIT_CANDIDATE,
            selection_boundary=DEFAULT_SELECTION,
            category_names=category_names,
            funding_source_names=funding_source_names,
            selection_policy=ReadSelectionPolicyVersion(args.read_selection_policy),
            checkpointer=checkpointer,
        )
        return dispatcher.dispatch(message, context=context)

    try:
        with open_postgres_checkpointer(database_url) as checkpointer:
            outcome = run(checkpointer)
    except ModelConnectionError as error:
        raise SystemExit(
            "this work unit needs the local inference server; start it with "
            "scripts/run_llama_server.sh (deterministic routes need no model)"
        ) from error
    return _dispatch_facts(outcome)


def _dispatch_facts(outcome: DispatchOutcome) -> dict[str, object]:
    """Render one typed dispatch outcome as safe administration JSON."""

    decision = outcome.routing
    return _routing_facts(decision) | {
        "answer": outcome.facts,
        "clarification_reason": outcome.clarification_reason,
        "executed_tool": (
            None if outcome.executed_tool is None else outcome.executed_tool.value
        ),
        "expense_id": None if outcome.expense_id is None else str(outcome.expense_id),
        "outcome": outcome.status.value,
        "proposal_id": (
            None if outcome.proposal_id is None else str(outcome.proposal_id)
        ),
        "settled_category_id": (
            None
            if outcome.settled_category_id is None
            else str(outcome.settled_category_id)
        ),
    }


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


def _profile_init() -> dict[str, object]:
    """Create the sole profile and its accepted taxonomy seed.

    V1 has one owner, so this refuses to add a second profile rather than
    leaving the installation ambiguous about which one owns the data.

    Returns:
        The profile identifier and the seeded taxonomy version.

    Raises:
        SystemExit: If a profile already exists.
    """

    repository = PostgresFinanceRepository(database_url_from_env())
    existing = repository.list_profiles()
    if existing:
        raise SystemExit(
            f"a profile already exists: {existing[0]}; V1 has a single owner"
        )
    profile = Profile(uuid4())
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=SystemClock().now(),
    )
    return {
        "profile_id": str(profile.id),
        "status": "created",
        "taxonomy_version": TAXONOMY_VERSION,
    }


def _profile_list() -> dict[str, object]:
    """List profiles and the funding sources each one has configured.

    Returns:
        One entry per profile with its sources, so an operator can find the
        identifier the other commands require without querying the database.
    """

    repository = PostgresFinanceRepository(database_url_from_env())
    profiles = []
    for profile_id in repository.list_profiles():
        profiles.append(
            {
                "funding_sources": [
                    {
                        "funding_class": item.funding_class.value,
                        "id": str(item.id),
                        "is_default": item.is_default,
                        "name": item.name,
                        "status": item.status.value,
                    }
                    for item in repository.get_funding_sources(profile_id)
                ],
                "profile_id": str(profile_id),
            }
        )
    return {"profiles": profiles}


def _funding_source_add(args: argparse.Namespace) -> dict[str, object]:
    """Add one funding source to an existing profile.

    Args:
        args: Parsed `funding-source-add` arguments.

    Returns:
        Safe facts about the created source.

    Raises:
        SystemExit: If the profile does not exist, the name is blank, the name
            repeats an existing source, or a different default already exists.
    """

    repository = PostgresFinanceRepository(database_url_from_env())
    if not repository.profile_exists(args.profile_id):
        raise SystemExit("active profile does not exist")
    name = args.name.strip()
    if not name:
        raise SystemExit("--name cannot be empty")
    existing = repository.get_funding_sources(args.profile_id)
    folded = normalize_owner_text(name)
    if any(normalize_owner_text(item.name) == folded for item in existing):
        raise SystemExit(f"a funding source named {name!r} already exists")
    if args.is_default and any(item.is_default for item in existing):
        raise SystemExit("a default funding source already exists; omit --default")
    source = FundingSource(
        id=uuid4(),
        profile_id=args.profile_id,
        name=name,
        funding_class=FundingClass(args.funding_class),
        balance_mode=BalanceMode(args.balance_mode),
        is_default=args.is_default,
    )
    repository.create_funding_source(source, occurred_at=SystemClock().now())
    return {
        "funding_class": source.funding_class.value,
        "id": str(source.id),
        "is_default": source.is_default,
        "name": source.name,
        "status": "created",
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
    if args.command == "profile-init":
        print(json.dumps(_profile_init(), sort_keys=True))
        return
    if args.command == "profile-list":
        print(json.dumps(_profile_list(), sort_keys=True))
        return
    if args.command == "funding-source-add":
        print(json.dumps(_funding_source_add(args), sort_keys=True))
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
