"""An in-memory implementation of the tracing port for unit tests.

It honours the same contract as the Langfuse adapter, including the rule that
an exception escaping a block marks the observation failed and propagates, so a
test observes what a component records without reaching a trace store.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from money_tracker.tracing import ModelCallRequest


@dataclass
class RecordedObservation:
    """Everything recorded on one span or generation.

    Attributes:
        name: Span name, or `model:<boundary>` for a generation.
        input: Input supplied when the observation opened.
        metadata: Metadata supplied when it opened, then merged updates.
        trace_seed: Seed supplied for a root span.
        request: Complete request, for a generation.
        output: Recorded result.
        failure: Recorded reason code and exception class.
        completion: Recorded completion facts, for a generation.
        parse: Recorded parse outcome, for a generation.
        recording: Whether the recorder reports that it records.
    """

    name: str
    input: object = None
    metadata: dict[str, object] = field(default_factory=dict)
    trace_seed: str | None = None
    request: ModelCallRequest | None = None
    output: object = None
    failure: tuple[str, str | None] | None = None
    completion: dict[str, object] | None = None
    parse: dict[str, object] | None = None
    recording: bool = True

    def record_output(self, output: object) -> None:
        """Store the result."""

        self.output = output

    def record_metadata(self, metadata: Mapping[str, object]) -> None:
        """Merge facts."""

        self.metadata.update(metadata)

    def record_failure(
        self, reason_code: str, *, exception_type: str | None = None
    ) -> None:
        """Store the failure."""

        self.failure = (reason_code, exception_type)

    def record_completion(
        self,
        *,
        completion: str,
        usage: Mapping[str, int] | None,
        response_metadata: Mapping[str, object],
    ) -> None:
        """Store the completion facts."""

        self.completion = {
            "completion": completion,
            "usage": None if usage is None else dict(usage),
            "response_metadata": dict(response_metadata),
        }

    def record_parse(
        self,
        *,
        contract: Mapping[str, object] | None,
        validation_error: str | None,
    ) -> None:
        """Store the parse outcome."""

        self.parse = {
            "contract": None if contract is None else dict(contract),
            "validation_error": validation_error,
        }


class RecordingGraphHandler(BaseCallbackHandler):
    """A real LangChain callback handler remembering which runs started."""

    def __init__(self, graph: str) -> None:
        """Bind the handler to the graph it was requested for.

        Args:
            graph: Graph name passed to `graph_config`.
        """

        self.graph = graph
        self.runs: list[str] = []

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember the started run's name."""

        self.runs.append(str(kwargs.get("name")))


@dataclass
class InMemoryTracer:
    """Tracer keeping every observation and graph configuration request.

    Attributes:
        observations: Observations in the order they opened.
        graph_configs: Graph names for which configuration was requested.
        handlers: Callback handlers returned, in request order.
        shutdowns: How many times shutdown was called.
    """

    observations: list[RecordedObservation] = field(default_factory=list)
    graph_configs: list[str] = field(default_factory=list)
    handlers: list[RecordingGraphHandler] = field(default_factory=list)
    shutdowns: int = 0
    enabled: bool = True

    @contextmanager
    def span(
        self,
        name: str,
        *,
        input: object = None,
        metadata: Mapping[str, object] | None = None,
        trace_seed: str | None = None,
    ) -> Generator[RecordedObservation]:
        """Open and keep one span."""

        observation = RecordedObservation(
            name=name,
            input=input,
            metadata=dict(metadata or {}),
            trace_seed=trace_seed,
        )
        with self._open(observation):
            yield observation

    @contextmanager
    def model_call(self, request: ModelCallRequest) -> Generator[RecordedObservation]:
        """Open and keep one generation."""

        observation = RecordedObservation(
            name=f"model:{request.boundary}", request=request
        )
        with self._open(observation):
            yield observation

    def graph_config(
        self, *, graph: str, include_model_runs: bool = False
    ) -> dict[str, Any]:
        """Return a real recording callback, the only key the port may add."""

        handler = RecordingGraphHandler(graph)
        self.graph_configs.append(graph)
        self.handlers.append(handler)
        return {"callbacks": [handler]}

    def shutdown(self) -> None:
        """Count the shutdown."""

        self.shutdowns += 1

    def named(self, name: str) -> list[RecordedObservation]:
        """Return the observations carrying one name."""

        return [item for item in self.observations if item.name == name]

    @contextmanager
    def _open(self, observation: RecordedObservation) -> Generator[None]:
        """Keep an observation and record an escaping exception as a failure."""

        self.observations.append(observation)
        try:
            yield
        except BaseException as error:
            if observation.failure is None:
                observation.record_failure(
                    "exception", exception_type=type(error).__name__
                )
            raise
