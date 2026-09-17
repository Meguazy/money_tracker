"""Langfuse implementation of the tracing port.

This is the only module in the application that imports the Langfuse SDK, and
it is imported only when tracing is switched on, so a disabled process never
loads the SDK, registers an OpenTelemetry provider, or opens an exporter.

Three choices keep the store faithful without letting it change behaviour:

- Graphs and their nodes are recorded by the SDK's LangChain callback, but the
  callback ignores model runs. The structured-output adapter records each
  request itself, because only it knows the complete `response_format`, the
  attempt number, and how the completion fared against its contract, and a
  second generation for the same request would double every token count.
- No exception reaches an SDK context manager. OpenTelemetry would otherwise
  attach the exception message and stack trace to the span, and a provider
  error can carry a URL. A failure is recorded as a stable reason code and the
  exception's class name, then re-raised unchanged.
- An SDK fault never becomes an application fault. Starting or ending an
  observation is guarded; the operation it describes runs either way.

The adapter also owns its OpenTelemetry provider instead of letting the SDK
register a global one. That keeps tracing from altering process-wide state,
and it lets `shutdown` deliver buffered spans within a stated bound. The SDK's
own shutdown is deliberately not called: it joins internal queues without a
timeout and, once the SDK is shared within a process, can wait forever.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Literal
from uuid import UUID

from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from money_tracker import __version__
from money_tracker.structured_logging import get_logger
from money_tracker.tracing.ports import (
    EvaluationPublication,
    EvaluationRecord,
    ModelCallObservation,
    ModelCallRequest,
    TraceObservation,
)
from money_tracker.tracing.redaction import TraceRedactor
from money_tracker.tracing.settings import TracingSettings

_LOGGER = get_logger(__name__)

IdentitySource = Callable[[], Mapping[str, object]]


def trace_id_for(seed: str) -> str:
    """Derive the Langfuse trace identifier for a durable work-unit identity.

    Every process that handles the same work unit derives the same identifier,
    which is what joins their observations into one trace.

    Args:
        seed: Durable identity such as a channel source reference.

    Returns:
        A 32-character lowercase hexadecimal trace identifier.
    """

    return Langfuse.create_trace_id(seed=seed)


class _Observation:
    """Recorder over one SDK observation that never raises into its caller."""

    recording = True

    def __init__(self, observation: Any) -> None:
        """Wrap an SDK span or generation.

        Args:
            observation: Started SDK observation, or None when starting failed.
        """

        self._observation = observation
        self.failed = False

    def _update(self, **fields: Any) -> None:
        """Apply one SDK update, recording rather than raising an SDK fault."""

        if self._observation is None:
            return
        try:
            self._observation.update(**fields)
        except Exception as error:
            _LOGGER.warning(
                "tracing.observation.update_failed",
                exception_type=type(error).__name__,
            )

    def record_output(self, output: object) -> None:
        """Attach the operation's result."""

        self._update(output=output)

    def record_metadata(self, metadata: Mapping[str, object]) -> None:
        """Attach facts learned while the operation ran."""

        self._update(metadata=dict(metadata))

    def record_failure(
        self, reason_code: str, *, exception_type: str | None = None
    ) -> None:
        """Mark the observation failed with a reason code and exception class."""

        self.failed = True
        self._update(
            level="ERROR",
            status_message=reason_code
            if exception_type is None
            else f"{reason_code}:{exception_type}",
        )

    def record_completion(
        self,
        *,
        completion: str,
        usage: Mapping[str, int] | None,
        response_metadata: Mapping[str, object],
    ) -> None:
        """Attach the raw completion, token usage, and provider facts."""

        fields: dict[str, Any] = {
            "output": completion,
            "metadata": {"response": dict(response_metadata)},
        }
        if usage:
            fields["usage_details"] = {
                "input": int(usage.get("input_tokens", 0)),
                "output": int(usage.get("output_tokens", 0)),
                "total": int(usage.get("total_tokens", 0)),
            }
        self._update(**fields)

    def record_parse(
        self,
        *,
        contract: Mapping[str, object] | None,
        validation_error: str | None,
    ) -> None:
        """Attach the parsed contract or the validation summary."""

        self._update(
            metadata={
                "parse": {
                    "valid": contract is not None,
                    "contract": None if contract is None else dict(contract),
                    "validation_error": validation_error,
                }
            }
        )


class _GraphCallbackHandler(CallbackHandler):
    """Record graph and node runs, leaving model runs to the adapter."""

    def __init__(self, *, public_key: str, graph: str) -> None:
        """Bind the handler to this process's client and a graph name.

        Args:
            public_key: Project key selecting the configured client.
            graph: Stable graph name given to the root span.
        """

        super().__init__(public_key=public_key)
        self._graph = graph

    @property
    def ignore_llm(self) -> bool:
        """Leave completion runs to the structured-output adapter."""

        return True

    @property
    def ignore_chat_model(self) -> bool:
        """Leave chat model runs to the structured-output adapter."""

        return True

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Name the root run after the graph instead of the framework default."""

        if parent_run_id is None:
            kwargs["name"] = f"graph:{self._graph}"
        return super().on_chain_start(
            serialized,
            inputs,
            run_id=run_id,
            parent_run_id=parent_run_id,
            tags=tags,
            metadata=metadata,
            **kwargs,
        )

    def _get_error_level_and_status_message(
        self, error: BaseException
    ) -> tuple[Literal["DEFAULT", "ERROR"], str]:
        """Report the exception class only, never its message."""

        level, _message = super()._get_error_level_and_status_message(error)
        return level, type(error).__name__


_PROVIDERS: dict[str, TracerProvider] = {}
"""One OpenTelemetry provider per Langfuse project key in this process.

The SDK keeps one set of resources per project key and ignores the arguments of
any later client for the same key, so a second tracer must flush the provider
the first one registered rather than an unused one of its own.
"""


def _provider_for(settings: TracingSettings) -> TracerProvider:
    """Return this process's provider for the configured project."""

    provider = _PROVIDERS.get(settings.public_key)
    if provider is None:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "money-tracker",
                    "service.version": __version__,
                    "langfuse.environment": settings.environment,
                    "langfuse.release": __version__,
                }
            )
        )
        _PROVIDERS[settings.public_key] = provider
    return provider


class LangfuseTracer:
    """Tracer and evaluation publisher for the local Langfuse instance.

    It implements both narrow ports: the runtime tracing port entry points
    inject, and the evaluation publication port the evaluation command uses, so
    one module owns every use of the SDK.
    """

    enabled = True

    def __init__(
        self,
        settings: TracingSettings,
        *,
        identity: IdentitySource,
        redactor: TraceRedactor,
    ) -> None:
        """Create the process's dedicated client.

        Args:
            settings: Enabled loopback settings.
            identity: Source of the serving model artifact's identity.
            redactor: Last-line removal of forbidden values, applied by the SDK
                to every input, output, and metadata value.
        """

        self._public_key = settings.public_key
        self._identity = identity
        self._flush_timeout_millis = settings.export_timeout_seconds * 1000
        self._provider = _provider_for(settings)
        self._client = Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            base_url=settings.base_url,
            timeout=settings.export_timeout_seconds,
            environment=settings.environment,
            release=__version__,
            sample_rate=1.0,
            tracing_enabled=True,
            mask=redactor,
            tracer_provider=self._provider,
        )
        _LOGGER.info(
            "tracing.enabled",
            environment=settings.environment,
            export_timeout_seconds=settings.export_timeout_seconds,
        )

    @contextmanager
    def span(
        self,
        name: str,
        *,
        input: object = None,
        metadata: Mapping[str, object] | None = None,
        trace_seed: str | None = None,
        trace_name: str | None = None,
    ) -> Generator[TraceObservation]:
        """Open a current span, optionally rooted at a seeded trace identifier.

        A seeded span starts a stage of a trace other processes may already
        have written to, so its trace name is propagated rather than inferred
        from whichever root observation the store received first.
        """

        arguments: dict[str, Any] = {
            "name": name,
            "as_type": "span",
            "input": input,
            "metadata": None if metadata is None else dict(metadata),
        }
        if trace_seed is not None:
            arguments["trace_context"] = {"trace_id": trace_id_for(trace_seed)}
        with self._current(arguments, trace_name=trace_name) as observation:
            yield observation

    @contextmanager
    def model_call(self, request: ModelCallRequest) -> Generator[ModelCallObservation]:
        """Open a generation carrying the complete request and model identity."""

        identity = self._safe_identity()
        arguments: dict[str, Any] = {
            "name": f"model:{request.boundary}",
            "as_type": "generation",
            "input": {
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "response_format": dict(request.response_format),
            },
            "model": str(
                identity.get("model_alias")
                or request.parameters.get("model_name")
                or ""
            ),
            "model_parameters": {
                key: value
                for key, value in request.parameters.items()
                if isinstance(value, str | int | float | bool)
            },
            "metadata": {
                "boundary": request.boundary,
                "attempt": request.attempt,
                "repair": request.attempt > 1,
                "request_parameters": dict(request.parameters),
                "model_artifact": identity,
            },
        }
        with self._current(arguments) as observation:
            yield observation

    def graph_config(
        self, *, graph: str, include_model_runs: bool = False
    ) -> dict[str, Any]:
        """Return only callbacks, which LangGraph never persists."""

        try:
            handler: CallbackHandler = (
                CallbackHandler(public_key=self._public_key)
                if include_model_runs
                else _GraphCallbackHandler(public_key=self._public_key, graph=graph)
            )
        except Exception as error:
            _LOGGER.warning(
                "tracing.graph_handler.failed", exception_type=type(error).__name__
            )
            return {}
        return {"callbacks": [handler]}

    def shutdown(self) -> None:
        """Deliver buffered spans, waiting no longer than the export bound.

        A stopped or slow store costs at most that bound; spans it could not
        take are dropped and the drop is recorded, never retried later.
        """

        self._flush_spans()

    def publish(self, publication: EvaluationPublication) -> bool:
        """Export aggregate evaluation evidence as one trace, then flush it.

        The root and each child are evaluator observations; their scores are
        attached to them. Scores travel on the SDK's own ingestion queue, whose
        flush has no timeout, so that flush runs on a daemon thread the caller
        waits for no longer than the export bound.

        Args:
            publication: Aggregate evidence to publish.

        Returns:
            True when every span and score was delivered within the bound.
        """

        root = publication.root
        arguments = self._evaluator_arguments(root)
        arguments["trace_context"] = {"trace_id": trace_id_for(publication.trace_seed)}
        with self._current(arguments, trace_name=publication.trace_name) as recorded:
            recorded.record_output(dict(root.output))
            self._score(root.scores)
            for child in publication.children:
                with self._current(self._evaluator_arguments(child)) as observation:
                    observation.record_output(dict(child.output))
                    self._score(child.scores)
        return self._flush_scores() and self._flush_spans()

    @staticmethod
    def _evaluator_arguments(record: EvaluationRecord) -> dict[str, Any]:
        """Build the SDK arguments of one evaluator observation."""

        return {
            "name": record.name,
            "as_type": "evaluator",
            "input": dict(record.input),
            "metadata": dict(record.metadata),
        }

    def _score(self, scores: Mapping[str, float]) -> None:
        """Attach numeric scores to the current observation, isolating faults."""

        for name, value in scores.items():
            try:
                self._client.score_current_span(
                    name=name, value=float(value), data_type="NUMERIC"
                )
            except Exception as error:
                _LOGGER.warning(
                    "tracing.score.failed", exception_type=type(error).__name__
                )

    def _flush_scores(self) -> bool:
        """Deliver queued scores, waiting no longer than the export bound."""

        finished = threading.Event()
        outcome = {"delivered": False}

        def flush() -> None:
            try:
                self._client.flush()
                outcome["delivered"] = True
            except Exception as error:
                _LOGGER.warning(
                    "tracing.flush.failed", exception_type=type(error).__name__
                )
            finally:
                finished.set()

        threading.Thread(
            target=flush, name="money-tracker-score-flush", daemon=True
        ).start()
        if not finished.wait(self._flush_timeout_millis / 1000):
            _LOGGER.warning(
                "tracing.flush.incomplete", duration_ms=self._flush_timeout_millis
            )
            return False
        return outcome["delivered"]

    def _flush_spans(self) -> bool:
        """Force-flush this process's provider within the export bound.

        Returns:
            True when every buffered span was delivered.
        """

        started = time.perf_counter()
        try:
            delivered = self._provider.force_flush(
                timeout_millis=self._flush_timeout_millis
            )
        except Exception as error:
            _LOGGER.warning("tracing.flush.failed", exception_type=type(error).__name__)
            return False
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        if not delivered:
            _LOGGER.warning("tracing.flush.incomplete", duration_ms=duration_ms)
            return False
        _LOGGER.debug("tracing.flush.completed", duration_ms=duration_ms)
        return True

    def _safe_identity(self) -> Mapping[str, object]:
        """Resolve model identity without letting a fault reach the call."""

        try:
            return self._identity()
        except Exception as error:
            _LOGGER.warning(
                "tracing.identity.failed", exception_type=type(error).__name__
            )
            return {"metadata_complete": False}

    @contextmanager
    def _current(
        self, arguments: dict[str, Any], *, trace_name: str | None = None
    ) -> Generator[_Observation]:
        """Hold one observation current for the block, isolating SDK faults.

        The SDK context managers are always exited as if the block succeeded,
        so OpenTelemetry never records the exception text or stack trace; the
        failure is recorded on the observation as a reason code instead.

        Args:
            arguments: Keyword arguments for the SDK observation.
            trace_name: Trace name to propagate to this observation and every
                observation opened inside it, when given.
        """

        manager: AbstractContextManager[Any] | None
        try:
            manager = self._client.start_as_current_observation(**arguments)
            observation = _Observation(manager.__enter__())
        except Exception as error:
            _LOGGER.warning(
                "tracing.observation.start_failed",
                exception_type=type(error).__name__,
            )
            manager = None
            observation = _Observation(None)
        propagation: AbstractContextManager[Any] | None = None
        if manager is not None and trace_name is not None:
            try:
                propagation = propagate_attributes(trace_name=trace_name)
                propagation.__enter__()
            except Exception as error:
                _LOGGER.warning(
                    "tracing.observation.propagation_failed",
                    exception_type=type(error).__name__,
                )
                propagation = None
        try:
            yield observation
        except BaseException as error:
            if not observation.failed:
                observation.record_failure(
                    "exception", exception_type=type(error).__name__
                )
            raise
        finally:
            for context in (propagation, manager):
                if context is None:
                    continue
                try:
                    context.__exit__(None, None, None)
                except Exception as error:
                    _LOGGER.warning(
                        "tracing.observation.end_failed",
                        exception_type=type(error).__name__,
                    )


__all__ = ["LangfuseTracer", "trace_id_for"]
