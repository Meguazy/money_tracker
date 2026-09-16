"""Production entry point for the sequential workflow worker.

The worker runs as its own process so that Telegram ingestion and finance
processing fail independently. It holds no Bot API credential: everything it
needs arrives through durable messaging state written by the gateway.

It opens the tracer the switch selects once, injects it into the model adapter,
the dispatcher, and the worker, and flushes it within the export bound after the
loop has honored a stop request. Spans are exported on the SDK's background
thread while it runs, so no claim waits for the trace store.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from langgraph.checkpoint.base import BaseCheckpointSaver
from psycopg import Error as PsycopgError

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
    FinanceService,
    IdGenerator,
    ReportingService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import CategoryStatus, FundingSourceStatus
from money_tracker.persistence.database import (
    database_url_from_env,
    open_postgres_checkpointer,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.presentation import TelegramResponseRenderer
from money_tracker.process_exit import exit_code_for, failure_reason, shutdown_event
from money_tracker.runtime import (
    LocalStructuredOutputModel,
    build_workflow_dispatcher,
)
from money_tracker.runtime.read_graph import DEFAULT_SELECTION
from money_tracker.runtime.trace_metadata import workflow_version_set
from money_tracker.structured_logging import configure_logging, get_logger
from money_tracker.telegram.config import TelegramSettings
from money_tracker.tracing import DISABLED_TRACER, Tracer, open_tracer
from money_tracker.worker.sequential import SequentialWorker
from money_tracker.workflows import (
    CATEGORY_FIT,
    DEFAULT_READ_SELECTION_POLICY,
    EXTRACTION,
    ROUTER,
    TOOL_SELECTION,
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    StructuredOutputModel,
    default_prompt,
)
from money_tracker.workflows.candidates import (
    DEFAULT_CATEGORY_FIT_CANDIDATE,
    DEFAULT_EXTRACTION_CANDIDATE,
)

_LOGGER = get_logger("money_tracker.worker.cli")
_IDLE_SECONDS = 1.0
_MINIMUM_CONFIDENCE = 0.5


def worker_version_set() -> dict[str, object]:
    """Describe the prompts, contracts, and policies the worker composes.

    Returns:
        The version set recorded on every dispatch span, built from the same
        promoted defaults the dispatcher assembly uses.
    """

    return workflow_version_set(
        router_prompt=default_prompt(ROUTER),
        minimum_confidence=_MINIMUM_CONFIDENCE,
        extraction=DEFAULT_EXTRACTION_CANDIDATE,
        category_fit=DEFAULT_CATEGORY_FIT_CANDIDATE,
        selection=DEFAULT_SELECTION,
        selection_policy=DEFAULT_READ_SELECTION_POLICY,
    )


@dataclass(slots=True)
class _CurrentTaxonomyDispatcher:
    """Assemble the specialists against the owner's state as it is now.

    The read-selection policy receives the active category and funding-source
    names when the graphs are compiled, so a long-lived dispatcher would keep
    answering with the taxonomy it started with. An approved review changes
    that taxonomy between two messages, which is exactly the case this process
    exists to serve, so the assembly is rebuilt for each work unit.
    """

    repository: PostgresFinanceRepository
    model: StructuredOutputModel
    ids: IdGenerator
    checkpointer: BaseCheckpointSaver[str]
    tracer: Tracer = DISABLED_TRACER
    trace_metadata: Mapping[str, object] = field(default_factory=worker_version_set)

    def dispatch(
        self,
        message: ChannelMessage,
        *,
        context: DispatchContext,
    ) -> DispatchOutcome:
        """Build the current assembly and route exactly one work unit.

        Args:
            message: Normalized inbound work unit.
            context: Trusted owner scope and correlations.

        Returns:
            The typed outcome of the single specialist that ran.

        Raises:
            RuntimeError: If the work unit carries no trusted profile scope.
        """

        profile_id = context.profile_id
        if profile_id is None:
            raise RuntimeError("a durable work unit always carries its profile")
        repository = self.repository
        clock = SystemClock()
        dispatcher = build_workflow_dispatcher(
            model=self.model,
            configuration=repository,
            finance=FinanceService(repository, clock=clock, ids=self.ids),
            proposals=CategoryProposalService(repository, clock=clock, ids=self.ids),
            review=CategoryReviewService(
                repository, repository, clock=clock, ids=self.ids
            ),
            reporting=ReportingService(repository),
            ids=self.ids,
            router_instructions=default_prompt(ROUTER).text,
            minimum_confidence=_MINIMUM_CONFIDENCE,
            extraction_instructions=default_prompt(EXTRACTION).text,
            category_instructions=default_prompt(CATEGORY_FIT).text,
            selection_instructions=default_prompt(TOOL_SELECTION).text,
            extraction_candidate=DEFAULT_EXTRACTION_CANDIDATE,
            category_candidate=DEFAULT_CATEGORY_FIT_CANDIDATE,
            selection_boundary=DEFAULT_SELECTION,
            category_names=tuple(
                category.name
                for category in repository.get_taxonomy(profile_id).categories
                if category.status is CategoryStatus.ACTIVE
            ),
            funding_source_names=tuple(
                source.name
                for source in repository.get_funding_sources(profile_id)
                if source.status is FundingSourceStatus.ACTIVE
            ),
            checkpointer=self.checkpointer,
            tracer=self.tracer,
            trace_metadata=self.trace_metadata,
        )
        return dispatcher.dispatch(message, context=context)


def _parser() -> argparse.ArgumentParser:
    """Build the sequential worker command parser."""

    parser = argparse.ArgumentParser(
        description="Process durable Telegram work units in order"
    )
    parser.add_argument(
        "command",
        choices=("run", "process-once"),
        help="process one eligible work unit, or run until stopped",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one safely configured sequential worker operation.

    Args:
        argv: Optional command arguments in place of process arguments.

    Raises:
        SystemExit: If configuration, pairing, or database setup fails.
    """

    args = _parser().parse_args(argv)
    configure_logging()
    tracer: Tracer = DISABLED_TRACER
    try:
        settings = TelegramSettings.from_env()
        database_url = database_url_from_env()
        finance = PostgresFinanceRepository(database_url)
        messaging = PostgresMessagingRepository(database_url)
        if messaging.owner_binding(settings.bot_key) is None:
            _LOGGER.error("worker.owner_binding.missing", bot_key=settings.bot_key)
            raise SystemExit(
                "this bot has no owner binding; pair the owner before processing"
            )
        ids = UUID4Generator()
        tracer = open_tracer(identity_in_background=args.command == "run")
        with open_postgres_checkpointer(database_url) as checkpointer:
            worker = SequentialWorker(
                bot_key=settings.bot_key,
                repository=messaging,
                configuration=finance,
                review=CategoryReviewService(
                    finance, finance, clock=SystemClock(), ids=ids
                ),
                dispatcher=_CurrentTaxonomyDispatcher(
                    repository=finance,
                    model=LocalStructuredOutputModel(tracer=tracer),
                    ids=ids,
                    checkpointer=checkpointer,
                    tracer=tracer,
                ),
                renderer=TelegramResponseRenderer(),
                clock=SystemClock(),
                tracer=tracer,
            )
            if args.command == "process-once":
                processed = worker.process_once()
                print(
                    json.dumps(
                        {
                            "environment": settings.environment.value,
                            "processed": processed,
                        },
                        sort_keys=True,
                    )
                )
                return
            _run_forever(worker)
    except (
        LookupError,
        PsycopgError,
        RuntimeError,
        ValueError,
    ) as error:
        _LOGGER.exception(
            "worker.command.failed",
            command=args.command,
            reason=failure_reason(error),
        )
        print(f"Workflow worker failed: {failure_reason(error)}", file=sys.stderr)
        raise SystemExit(int(exit_code_for(error))) from None
    finally:
        # Bounded by the export timeout whether the store is up, slow, or
        # stopped, which the unit's stop timeout comfortably covers.
        tracer.shutdown()


def _run_forever(worker: SequentialWorker) -> None:
    """Drain the queue in order, idling only when nothing is eligible.

    A failing work unit is not a failing process: the worker already turned it
    into a durable retry or dead letter, so the loop keeps going. Only a broken
    dependency propagates, because a supervisor restarting the process is the
    honest response to a database or credential problem.

    A stop request is honored between work units rather than inside one, so the
    unit the worker already claimed reaches its terminal transaction instead of
    being replayed after its lease expires.
    """

    _LOGGER.info("worker.loop.started", idle_seconds=_IDLE_SECONDS)
    stop = shutdown_event()
    while not stop.is_set():
        if not worker.process_once():
            stop.wait(_IDLE_SECONDS)
    _LOGGER.info("worker.loop.stopped")
