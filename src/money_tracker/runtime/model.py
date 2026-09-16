"""Local structured-output adapter for the workflow model boundary.

This is the only place a workflow boundary meets an inference provider. It uses
the exact mechanism the Step 1 capability screen validated against the real
llama.cpp build: an OpenAI-compatible `response_format` of type `json_schema`
carrying the contract's own JSON Schema with `strict` enabled. There is no
silent fallback to plain JSON mode or to prompt-only instruction, because AD-012
requires an unsupported capability to be disabled explicitly.

The adapter returns raw completion text. Parsing, the single corrective retry,
and the failure policy stay in `money_tracker.workflows.model`, so provider
behaviour cannot quietly change how many attempts a boundary gets.

It is also where each request is traced, because it is the only place that
holds the complete request: every turn, the bound `response_format`, and the
attempt. The injected tracer observes the request and never shapes it; the
turns and the bound schema are built before the tracer sees them, identically
whether tracing is on or off. When a trace is recording, the completion is
validated against its contract a second time, purely to record the parsed
contract or the field paths that will trigger the corrective retry. That parse
is the same deterministic `model_validate_json` the boundary applies, so it
cannot disagree with it, and its result never reaches the caller.
"""

from __future__ import annotations

from typing import Any

from langchain_core.exceptions import ModelConnectionError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from openai import APIStatusError
from pydantic import BaseModel, ValidationError

from money_tracker.llm import create_chat_model
from money_tracker.structured_logging import get_logger
from money_tracker.tracing import (
    DISABLED_TRACER,
    ModelCallObservation,
    ModelCallRequest,
    TraceMessage,
    Tracer,
)
from money_tracker.workflows import RepairRequest, summarize_validation_error

_LOGGER = get_logger("money_tracker.runtime.model")


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Render a contract's JSON Schema for strict constrained decoding.

    Every property of every object is marked required. A property a contract
    treats as optional already admits null, so requiring the key costs the
    model nothing and removes its ability to omit one: an optional-in-schema
    field is not enforced by the decoding grammar at all, and a model that
    forgets one turns an otherwise correct answer into a schema failure that
    burns the retry budget. Strict structured-output modes require this
    anyway.

    The capability screen has since measured both shapes against the text
    candidate and the reasoning holds. Leaving properties genuinely optional
    made the model *less* likely to leave a silent field empty, 0.4265 against
    0.7647, and its failures were overwhelmingly the empty strings a non-empty
    text constraint refuses, which is the retry-burning schema failure this
    rendering exists to avoid.

    Args:
        schema: Contract to render.

    Returns:
        A JSON Schema whose objects require every property they declare.
    """

    def require_all(node: Any) -> Any:
        if isinstance(node, dict):
            transformed = {key: require_all(value) for key, value in node.items()}
            properties = transformed.get("properties")
            if isinstance(properties, dict):
                transformed["required"] = sorted(properties)
            return transformed
        if isinstance(node, list):
            return [require_all(item) for item in node]
        return node

    rendered: Any = require_all(schema.model_json_schema())
    return dict(rendered)


REPAIR_INSTRUCTION = (
    "Your previous reply did not satisfy the required schema. "
    "Problems: {problems}. Reply again with only a JSON document that "
    "satisfies the schema. Do not explain the correction."
)
"""Follow-up turn used for the single corrective retry."""


class LocalStructuredOutputModel:
    """Produce one strict-schema completion from a local endpoint."""

    def __init__(
        self,
        chat_model: BaseChatModel | None = None,
        *,
        tracer: Tracer = DISABLED_TRACER,
    ) -> None:
        """Bind the adapter to a chat model, or build one from settings.

        Sampling parameters come from the injected model rather than being
        overridden here, so an evaluation run pins temperature and seed through
        configuration and records them in its manifest.

        Args:
            chat_model: Explicit chat model. Environment settings are used when
                omitted.
            tracer: Tracing port chosen by the entry point. The default records
                nothing, which is what every evaluation and test relies on.
        """

        self._chat_model = chat_model or create_chat_model()
        self._tracer = tracer

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: RepairRequest | None = None,
    ) -> str:
        """Request one completion constrained to the contract's JSON Schema.

        Args:
            instructions: Task contract for this boundary.
            message: Untrusted owner text to interpret.
            schema: Contract the completion must satisfy.
            repair: Prior rejection to correct, on the single retry only.

        Returns:
            Raw completion text for the caller to parse.

        Raises:
            RuntimeError: If the endpoint returns a non-text content block,
                which would mean the configured model is not usable for a
                structured boundary.
        """

        turns: list[BaseMessage] = [
            SystemMessage(content=instructions),
            HumanMessage(content=message),
        ]
        if repair is not None:
            turns.append(AIMessage(content=repair.invalid_output))
            turns.append(
                HumanMessage(
                    content=REPAIR_INSTRUCTION.format(problems=repair.validation_error)
                )
            )
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": strict_json_schema(schema),
            },
        }
        constrained = self._chat_model.bind(response_format=response_format)
        _LOGGER.debug(
            "inference.request.started",
            schema=schema.__name__,
            repair=repair is not None,
        )
        request = ModelCallRequest(
            boundary=schema.__name__,
            attempt=1 if repair is None else 2,
            messages=tuple(_trace_message(turn) for turn in turns),
            response_format=response_format,
            parameters=_request_parameters(self._chat_model),
        )
        with self._tracer.model_call(request) as call:
            content = self._invoke(constrained, turns, schema, call)
            if call.recording:
                _record_parse(call, schema, content)
        return content

    def _invoke(
        self,
        constrained: Any,
        turns: list[BaseMessage],
        schema: type[BaseModel],
        call: ModelCallObservation,
    ) -> str:
        """Send one prepared request and return its text completion.

        Args:
            constrained: Chat model with the strict `response_format` bound.
            turns: Complete ordered request turns.
            schema: Contract the completion must satisfy.
            call: Recorder for the request's generation.

        Returns:
            Raw completion text.

        Raises:
            ModelConnectionError: If the endpoint is unreachable or answered with
                a server-side status.
            APIStatusError: If the endpoint rejected the request itself.
            RuntimeError: If the endpoint returned a non-text content block.
        """

        try:
            response = constrained.invoke(turns)
        except APIStatusError as error:
            # llama.cpp accepts connections while it is still reading the model
            # off disk and answers 503 until it is ready. A refused connection
            # already arrives as `ModelConnectionError`, but this one did not,
            # so it escaped the worker's work-unit handling, killed the process
            # with a traceback, and exited 1 — which the unit reads as "an
            # operator must fix this" and refuses to restart. A transient
            # thirty-second startup therefore took the worker down until
            # someone noticed, with its claimed item stranded.
            #
            # Any server-side status means the endpoint is not answering for
            # reasons the caller cannot correct. A 4xx is the caller's own
            # request and must keep propagating.
            if error.status_code < 500:
                _LOGGER.error(
                    "inference.request.rejected",
                    schema=schema.__name__,
                    status_code=error.status_code,
                )
                call.record_failure(
                    f"endpoint_rejected_{error.status_code}",
                    exception_type=type(error).__name__,
                )
                raise
            _LOGGER.warning(
                "inference.endpoint.unavailable",
                schema=schema.__name__,
                status_code=error.status_code,
            )
            call.record_failure(
                f"endpoint_unavailable_{error.status_code}",
                exception_type=type(error).__name__,
            )
            raise ModelConnectionError(
                f"local inference endpoint returned {error.status_code}"
            ) from error
        except ModelConnectionError as error:
            call.record_failure(
                "endpoint_unreachable", exception_type=type(error).__name__
            )
            raise
        content = response.content
        if not isinstance(content, str):
            _LOGGER.error(
                "inference.response.not_text",
                schema=schema.__name__,
                content_type=type(content).__name__,
            )
            call.record_failure("response_not_text")
            raise RuntimeError(
                "structured boundary requires text content from the endpoint"
            )
        call.record_completion(
            completion=content,
            usage=_usage(response),
            response_metadata=dict(getattr(response, "response_metadata", {}) or {}),
        )
        # The completion is model output derived from owner text, so its length
        # is recorded and its content is not.
        _LOGGER.debug(
            "inference.request.completed",
            schema=schema.__name__,
            completion_characters=len(content),
        )
        return content


_TRACED_PARAMETERS = (
    "model_name",
    "temperature",
    "request_timeout",
    "max_retries",
    "seed",
    "top_p",
    "max_tokens",
)
"""Chat model attributes that shape a request, recorded beside it.

The endpoint URL and API key are also chat model attributes, which is why the
recorded set is an allowlist rather than everything the model carries.
"""


def _request_parameters(chat_model: BaseChatModel) -> dict[str, object]:
    """Read the sampling and transport parameters a request is sent with.

    Args:
        chat_model: Chat model the adapter invokes.

    Returns:
        Allowlisted parameters that are set, never a URL or credential.
    """

    parameters: dict[str, object] = {}
    for name in _TRACED_PARAMETERS:
        value = getattr(chat_model, name, None)
        if isinstance(value, str | int | float | bool):
            parameters[name] = value
    return parameters


def _trace_message(turn: BaseMessage) -> TraceMessage:
    """Describe one request turn by the role the endpoint receives."""

    roles = {"system": "system", "human": "user", "ai": "assistant"}
    return TraceMessage(role=roles.get(turn.type, turn.type), content=turn.text)


def _usage(response: Any) -> dict[str, int] | None:
    """Return the token counts the endpoint reported, when it reported any."""

    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return None
    return {
        key: int(value)
        for key, value in dict(usage).items()
        if key in {"input_tokens", "output_tokens", "total_tokens"}
    }


def _record_parse(
    call: ModelCallObservation, schema: type[BaseModel], content: str
) -> None:
    """Record whether a completion satisfies its contract, for the trace only.

    Args:
        call: Recorder for the request's generation.
        schema: Contract the boundary will validate against.
        content: Raw completion text.
    """

    try:
        parsed = schema.model_validate_json(content)
    except ValidationError as error:
        call.record_parse(
            contract=None, validation_error=summarize_validation_error(error)
        )
        return
    call.record_parse(contract=parsed.model_dump(mode="json"), validation_error=None)
