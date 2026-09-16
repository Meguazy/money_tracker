"""The tracing port that entry points inject into the inference path.

Runtime components record what they did through this narrow interface and never
through a tracing SDK. An entry point decides which implementation to inject,
so the same graph or model adapter traces under the worker, stays silent under
an evaluation, and cannot tell the difference.

The disabled implementation is the default everywhere. It returns an empty
graph configuration and records nothing, so a process with tracing off hands
LangGraph and the model endpoint exactly what it handed them before the port
existed.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Final, Protocol

TELEGRAM_UPDATE_TRACE: Final = "telegram-update"
"""Trace name shared by the gateway and worker stages of one Telegram update."""

ADMIN_ROUTE_TRACE: Final = "admin-route"
"""Trace name of one `money-tracker-admin route` work unit."""

WEB_ADVICE_TRACE: Final = "web-advice"
"""Trace name of one browser advice request."""


@dataclass(frozen=True, slots=True)
class TraceMessage:
    """One chat turn exactly as the adapter sends it.

    Attributes:
        role: OpenAI-compatible role name: `system`, `user`, or `assistant`.
        content: Complete turn text, owner content included.
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelCallRequest:
    """Everything one structured-output request carries to the endpoint.

    Attributes:
        boundary: Name of the contract the completion must satisfy.
        attempt: 1 for the initial request, 2 for the single corrective retry.
        messages: Complete ordered turns, including any repair turns.
        response_format: The `response_format` value bound to the request,
            with the full strict JSON Schema.
        parameters: Sampling and transport parameters of the chat model, such
            as the model alias, temperature, timeout, and retry budget. Never an
            endpoint URL or credential.
    """

    boundary: str
    attempt: int
    messages: tuple[TraceMessage, ...]
    response_format: Mapping[str, object]
    parameters: Mapping[str, object]


class TraceObservation(Protocol):
    """A recorder bound to one open span."""

    def record_output(self, output: object) -> None:
        """Attach the operation's result.

        Args:
            output: JSON-compatible result of the observed operation.
        """

    def record_metadata(self, metadata: Mapping[str, object]) -> None:
        """Attach facts learned while the operation ran.

        Args:
            metadata: JSON-compatible facts keyed by stable names.
        """

    def record_failure(
        self, reason_code: str, *, exception_type: str | None = None
    ) -> None:
        """Mark the operation as failed with a stable reason.

        Args:
            reason_code: Stable machine-readable failure reason.
            exception_type: Class name of the exception, when there was one.
                The exception text is deliberately not accepted, because a
                provider error can embed a URL.
        """


class ModelCallObservation(TraceObservation, Protocol):
    """A recorder bound to one model request."""

    @property
    def recording(self) -> bool:
        """Whether recorded facts go anywhere.

        A caller checks this before doing work whose only purpose is the trace,
        such as parsing a completion a second time.
        """

    def record_completion(
        self,
        *,
        completion: str,
        usage: Mapping[str, int] | None,
        response_metadata: Mapping[str, object],
    ) -> None:
        """Attach the raw completion and the provider's own report of it.

        Args:
            completion: Raw completion text exactly as returned.
            usage: Token counts reported by the endpoint, when it reported them.
            response_metadata: Provider response facts such as finish reason and
                reported model name.
        """

    def record_parse(
        self,
        *,
        contract: Mapping[str, object] | None,
        validation_error: str | None,
    ) -> None:
        """Attach how the completion fared against its contract.

        Args:
            contract: The parsed contract as JSON primitives, when it validated.
            validation_error: The field-path summary that would trigger or
                exhaust the corrective retry, when it did not.
        """


class Tracer(Protocol):
    """Narrow tracing port injected at every entry point."""

    @property
    def enabled(self) -> bool:
        """Whether this tracer records anything at all."""

    def span(
        self,
        name: str,
        *,
        input: object = None,
        metadata: Mapping[str, object] | None = None,
        trace_seed: str | None = None,
        trace_name: str | None = None,
    ) -> AbstractContextManager[TraceObservation]:
        """Open a span nested under the currently open one.

        An exception escaping the block marks the span failed and propagates.

        Args:
            name: Stable span name.
            input: JSON-compatible input of the operation.
            metadata: JSON-compatible facts known when the operation starts.
            trace_seed: Durable identity, such as a source reference, from
                which the trace identifier is derived. Supplied only by the
                component that owns a stage of the work unit, so every process
                handling the same unit lands in the same trace.
            trace_name: Stable name of the whole trace, given with a seed so
                that every process joining the trace names it the same way
                whichever stage reaches the store first.

        Returns:
            A context manager yielding the span's recorder.
        """

    def model_call(
        self, request: ModelCallRequest
    ) -> AbstractContextManager[ModelCallObservation]:
        """Open a generation for one request to the model endpoint.

        Args:
            request: Complete request payload and parameters.

        Returns:
            A context manager yielding the generation's recorder.
        """

    def graph_config(
        self, *, graph: str, include_model_runs: bool = False
    ) -> dict[str, Any]:
        """Return the configuration keys that trace one graph invocation.

        Only non-persisted keys are returned. LangGraph copies `metadata` and
        primitive `configurable` values into checkpoint metadata, so returning
        either would make tracing change what the checkpointer stores.

        Args:
            graph: Stable graph name used for the span.
            include_model_runs: Also record chat model runs from callbacks. Only
                a graph whose model calls bypass the structured-output adapter
                needs this; the adapter already records its own requests.

        Returns:
            Keys to merge into a `RunnableConfig`; empty when disabled.
        """

    def shutdown(self) -> None:
        """Deliver buffered observations within the export bound and stop."""


class _SilentObservation:
    """Recorder that discards everything, for the disabled tracer."""

    recording = False

    def record_output(self, output: object) -> None:
        """Discard a result."""

    def record_metadata(self, metadata: Mapping[str, object]) -> None:
        """Discard facts."""

    def record_failure(
        self, reason_code: str, *, exception_type: str | None = None
    ) -> None:
        """Discard a failure."""

    def record_completion(
        self,
        *,
        completion: str,
        usage: Mapping[str, int] | None,
        response_metadata: Mapping[str, object],
    ) -> None:
        """Discard a completion."""

    def record_parse(
        self,
        *,
        contract: Mapping[str, object] | None,
        validation_error: str | None,
    ) -> None:
        """Discard a parse outcome."""


_SILENT = _SilentObservation()


class DisabledTracer:
    """Tracer that records nothing and changes nothing it is handed."""

    enabled = False

    def span(
        self,
        name: str,
        *,
        input: object = None,
        metadata: Mapping[str, object] | None = None,
        trace_seed: str | None = None,
        trace_name: str | None = None,
    ) -> AbstractContextManager[TraceObservation]:
        """Return a context that records nothing."""

        return nullcontext(_SILENT)

    def model_call(
        self, request: ModelCallRequest
    ) -> AbstractContextManager[ModelCallObservation]:
        """Return a context that records nothing."""

        return nullcontext(_SILENT)

    def graph_config(
        self, *, graph: str, include_model_runs: bool = False
    ) -> dict[str, Any]:
        """Return no configuration, leaving a graph invocation unchanged."""

        return {}

    def shutdown(self) -> None:
        """Do nothing; nothing was buffered."""


DISABLED_TRACER: Tracer = DisabledTracer()
"""Shared disabled tracer used wherever no tracer is injected."""


__all__ = [
    "ADMIN_ROUTE_TRACE",
    "DISABLED_TRACER",
    "DisabledTracer",
    "ModelCallObservation",
    "ModelCallRequest",
    "TraceMessage",
    "TraceObservation",
    "TELEGRAM_UPDATE_TRACE",
    "Tracer",
    "WEB_ADVICE_TRACE",
]
