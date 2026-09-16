"""Tests for the local structured-output adapter's request shape."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langchain_core.exceptions import ModelConnectionError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from openai import APIStatusError
from pydantic import Field

from money_tracker.runtime import LocalStructuredOutputModel
from money_tracker.tracing import DISABLED_TRACER
from money_tracker.workflows import RepairRequest, RouteClassification
from tests.unit.money_tracker.tracing.memory_tracer import InMemoryTracer

COMPLETION = json.dumps(
    {"intent": "expense", "confidence": 0.94, "evidence": "12 euro"}
)


class RecordingChatModel(BaseChatModel):
    """A minimal real chat model that records requests and replays answers.

    This implements the LangChain contract rather than patching the adapter, so
    the test observes exactly the request the adapter builds.
    """

    completions: list[str] = Field(default_factory=list)
    requests: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        """Identify this model implementation to LangChain."""

        return "recording"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Record the request and return the next scripted completion."""

        self.requests.append({"messages": list(messages), "kwargs": dict(kwargs)})
        text = self.completions[len(self.requests) - 1]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def test_adapter_requests_strict_json_schema_for_the_contract() -> None:
    """Use the exact mechanism the Step 1 capability screen validated."""

    chat_model = RecordingChatModel(completions=[COMPLETION])
    adapter = LocalStructuredOutputModel(chat_model)

    adapter.complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
    )
    response_format = chat_model.requests[0]["kwargs"]["response_format"]

    rendered = response_format["json_schema"]["schema"]

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "RouteClassification"
    assert (
        rendered["properties"]
        == (RouteClassification.model_json_schema()["properties"])
    )


def test_every_property_is_required_for_constrained_decoding() -> None:
    """Force the model to emit every key rather than silently omit one.

    An optional-in-schema property is not enforced by the decoding grammar, so
    a model can drop it and turn an otherwise correct answer into a schema
    failure that burns the retry budget. Optional fields already admit null, so
    requiring the key costs nothing.
    """

    chat_model = RecordingChatModel(completions=[COMPLETION])

    LocalStructuredOutputModel(chat_model).complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
    )
    rendered = chat_model.requests[0]["kwargs"]["response_format"]["json_schema"][
        "schema"
    ]

    assert rendered["required"] == sorted(rendered["properties"])
    assert "unknowns" in rendered["required"]
    assert "unknowns" not in RouteClassification.model_json_schema()["required"]


def test_adapter_returns_raw_text_for_the_deterministic_parser() -> None:
    """Leave parsing and retry policy to the workflow boundary."""

    adapter = LocalStructuredOutputModel(RecordingChatModel(completions=[COMPLETION]))

    result = adapter.complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
    )

    assert result == COMPLETION


def test_first_attempt_sends_only_instructions_and_owner_text() -> None:
    """Keep the initial request to a system contract and the message."""

    chat_model = RecordingChatModel(completions=[COMPLETION])

    LocalStructuredOutputModel(chat_model).complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
    )
    messages = chat_model.requests[0]["messages"]

    assert [type(item) for item in messages] == [SystemMessage, HumanMessage]
    assert messages[0].content == "classify"
    assert messages[1].content == "ho speso 12 euro"


def test_repair_turn_quotes_the_rejection_and_asks_for_json_only() -> None:
    """Give the model its own rejected reply plus the stated problems."""

    chat_model = RecordingChatModel(completions=[COMPLETION])

    LocalStructuredOutputModel(chat_model).complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
        repair=RepairRequest(
            invalid_output="{}", validation_error="evidence: Field required"
        ),
    )
    messages = chat_model.requests[0]["messages"]

    assert [type(item) for item in messages] == [
        SystemMessage,
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert messages[2].content == "{}"
    assert "evidence: Field required" in messages[3].content
    assert "JSON" in messages[3].content


def test_non_text_content_is_refused_rather_than_coerced() -> None:
    """Fail explicitly when the endpoint cannot serve a structured boundary."""

    class BlockContentModel(RecordingChatModel):
        """Return a content block list instead of text."""

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            """Return structured content blocks rather than a string."""

            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content=[{"type": "text", "text": "{}"}])
                    )
                ]
            )

    adapter = LocalStructuredOutputModel(BlockContentModel())

    with pytest.raises(RuntimeError, match="requires text content"):
        adapter.complete_json(
            instructions="classify",
            message="ho speso 12 euro",
            schema=RouteClassification,
        )


class FailingChatModel(BaseChatModel):
    """A chat model that raises one provider status error per call."""

    status_code: int = 503

    @property
    def _llm_type(self) -> str:
        """Identify this model implementation to LangChain."""

        return "failing"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Fail the way the local endpoint does, with a real status error."""

        response = httpx.Response(
            self.status_code,
            request=httpx.Request("POST", "http://localhost:8080/v1/chat/completions"),
        )
        raise APIStatusError("endpoint failure", response=response, body=None)


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_an_unready_endpoint_is_a_connection_failure(status_code: int) -> None:
    """Let the worker retry a model that is merely still starting.

    llama.cpp accepts connections while reading the model off disk and answers
    503 until it is ready. That escaped the worker's work-unit handling, killed
    the process with a traceback and exit code 1, and the unit reads 1 as a
    problem an operator must fix and refuses to restart. A thirty-second
    startup took the worker down until someone noticed.
    """

    adapter = LocalStructuredOutputModel(FailingChatModel(status_code=status_code))

    with pytest.raises(ModelConnectionError):
        adapter.complete_json(
            instructions="classify",
            message="12 euro di pizza",
            schema=RouteClassification,
        )


def test_a_rejected_request_still_propagates() -> None:
    """Keep a caller-side status distinguishable from an unavailable endpoint.

    A 4xx says this request was wrong, which retrying cannot fix and which must
    not be reported as the endpoint being down.
    """

    adapter = LocalStructuredOutputModel(FailingChatModel(status_code=400))

    with pytest.raises(APIStatusError):
        adapter.complete_json(
            instructions="classify",
            message="12 euro di pizza",
            schema=RouteClassification,
        )


class ParameterizedRecordingChatModel(RecordingChatModel):
    """A recording model carrying the attributes a real client exposes.

    It holds an endpoint and a key exactly as `ChatOpenAI` does, so the test can
    prove neither reaches the recorded request parameters.
    """

    model_name: str = "local-model"
    temperature: float = 0.1
    request_timeout: float = 120.0
    max_retries: int = 2
    openai_api_base: str = "http://127.0.0.1:8080/v1"
    openai_api_key: str = "local-secret-api-key"
    usage: dict[str, int] = Field(default_factory=dict)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Record the request and answer with provider usage metadata."""

        self.requests.append({"messages": list(messages), "kwargs": dict(kwargs)})
        text = self.completions[len(self.requests) - 1]
        message = AIMessage(
            content=text,
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            },
            response_metadata={"finish_reason": "stop"},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


REPAIR = RepairRequest(
    invalid_output='{"intent": "spesa"}',
    validation_error="intent: Input should be 'expense'",
)


def _requests_for(tracer: Any) -> list[dict[str, Any]]:
    """Send an initial request and a repair through one adapter configuration."""

    chat_model = ParameterizedRecordingChatModel(completions=[COMPLETION, COMPLETION])
    adapter = LocalStructuredOutputModel(chat_model, tracer=tracer)
    for repair in (None, REPAIR):
        adapter.complete_json(
            instructions="classify",
            message="ho speso 12 euro",
            schema=RouteClassification,
            repair=repair,
        )
    return [
        {
            "messages": [(type(item), item.content) for item in request["messages"]],
            "kwargs": request["kwargs"],
        }
        for request in chat_model.requests
    ]


def test_tracing_does_not_change_what_is_sent_to_the_model() -> None:
    """Send byte-for-byte the same turns and schema whether or not a trace records.

    The integration suite repeats this over the wire against the real endpoint;
    this test pins the adapter's own contribution, repair turn included.
    """

    assert _requests_for(InMemoryTracer()) == _requests_for(DISABLED_TRACER)


def test_a_traced_request_records_the_complete_call() -> None:
    """Record every turn, the bound schema, parameters, completion, and parse."""

    tracer = InMemoryTracer()
    chat_model = ParameterizedRecordingChatModel(completions=[COMPLETION])
    LocalStructuredOutputModel(chat_model, tracer=tracer).complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
        repair=REPAIR,
    )

    (call,) = tracer.observations
    assert call.request is not None
    assert call.name == "model:RouteClassification"
    assert call.request.attempt == 2
    assert [(item.role, item.content) for item in call.request.messages] == [
        ("system", "classify"),
        ("user", "ho speso 12 euro"),
        ("assistant", '{"intent": "spesa"}'),
        ("user", chat_model.requests[0]["messages"][3].content),
    ]
    assert (
        call.request.response_format
        == chat_model.requests[0]["kwargs"]["response_format"]
    )
    assert call.request.parameters == {
        "model_name": "local-model",
        "temperature": 0.1,
        "request_timeout": 120.0,
        "max_retries": 2,
    }
    assert call.completion == {
        "completion": COMPLETION,
        "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        "response_metadata": {"finish_reason": "stop"},
    }
    assert call.parse == {
        "contract": {
            "intent": "expense",
            "confidence": 0.94,
            "evidence": "12 euro",
            "unknowns": [],
        },
        "validation_error": None,
    }


def test_a_traced_invalid_completion_records_the_validation_summary() -> None:
    """Record the field paths that will trigger the single corrective retry."""

    tracer = InMemoryTracer()
    invalid = '{"intent": "spesa", "confidence": 2}'
    result = LocalStructuredOutputModel(
        ParameterizedRecordingChatModel(completions=[invalid]), tracer=tracer
    ).complete_json(
        instructions="classify",
        message="ho speso 12 euro",
        schema=RouteClassification,
    )

    (call,) = tracer.observations
    assert result == invalid
    assert call.parse is not None
    assert call.parse["contract"] is None
    assert "intent" in str(call.parse["validation_error"])
    assert "spesa" not in str(call.parse["validation_error"])


@pytest.mark.parametrize(
    ("status_code", "expected", "reason"),
    [
        (503, ModelConnectionError, "endpoint_unavailable_503"),
        (400, APIStatusError, "endpoint_rejected_400"),
    ],
)
def test_a_traced_endpoint_failure_records_a_reason_code(
    status_code: int, expected: type[Exception], reason: str
) -> None:
    """Record why the call failed without the provider's error text."""

    tracer = InMemoryTracer()
    adapter = LocalStructuredOutputModel(
        FailingChatModel(status_code=status_code), tracer=tracer
    )

    with pytest.raises(expected):
        adapter.complete_json(
            instructions="classify",
            message="12 euro di pizza",
            schema=RouteClassification,
        )

    (call,) = tracer.observations
    assert call.failure == (reason, "APIStatusError")
    assert call.completion is None
