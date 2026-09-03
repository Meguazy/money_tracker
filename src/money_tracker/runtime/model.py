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
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from money_tracker.llm import create_chat_model
from money_tracker.workflows import RepairRequest


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

    def __init__(self, chat_model: BaseChatModel | None = None) -> None:
        """Bind the adapter to a chat model, or build one from settings.

        Sampling parameters come from the injected model rather than being
        overridden here, so an evaluation run pins temperature and seed through
        configuration and records them in its manifest.

        Args:
            chat_model: Explicit chat model. Environment settings are used when
                omitted.
        """

        self._chat_model = chat_model or create_chat_model()

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
        constrained = self._chat_model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strict_json_schema(schema),
                },
            }
        )
        content = constrained.invoke(turns).content
        if not isinstance(content, str):
            raise RuntimeError(
                "structured boundary requires text content from the endpoint"
            )
        return content
