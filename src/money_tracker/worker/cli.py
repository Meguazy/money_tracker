"""Production entry point for the sequential workflow worker.

The worker runs as its own process so that Telegram ingestion and finance
processing fail independently. It holds no Bot API credential: everything it
needs arrives through durable messaging state written by the gateway.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

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
from money_tracker.domain import CategoryStatus
from money_tracker.logging_safety import configure_safe_logging
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
from money_tracker.telegram.config import TelegramSettings
from money_tracker.worker.sequential import SequentialWorker
from money_tracker.workflows import (
    CATEGORY_FIT,
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

_LOGGER = logging.getLogger("money_tracker.worker.cli")
_IDLE_SECONDS = 1.0
_MINIMUM_CONFIDENCE = 0.5


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
                source.name for source in repository.get_funding_sources(profile_id)
            ),
            checkpointer=self.checkpointer,
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
    configure_safe_logging()
    try:
        settings = TelegramSettings.from_env()
        database_url = database_url_from_env()
        finance = PostgresFinanceRepository(database_url)
        messaging = PostgresMessagingRepository(database_url)
        if messaging.owner_binding(settings.bot_key) is None:
            raise SystemExit(
                "this bot has no owner binding; pair the owner before processing"
            )
        ids = UUID4Generator()
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
                    model=LocalStructuredOutputModel(),
                    ids=ids,
                    checkpointer=checkpointer,
                ),
                renderer=TelegramResponseRenderer(),
                clock=SystemClock(),
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
        print(f"Workflow worker failed: {failure_reason(error)}", file=sys.stderr)
        raise SystemExit(int(exit_code_for(error))) from None


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

    _LOGGER.info("Sequential workflow worker started")
    stop = shutdown_event()
    while not stop.is_set():
        if not worker.process_once():
            stop.wait(_IDLE_SECONDS)
    _LOGGER.info("Sequential workflow worker stopped")
