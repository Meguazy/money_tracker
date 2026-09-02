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

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from money_tracker.llm import create_chat_model
from money_tracker.workflows import RepairRequest

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
                    "schema": schema.model_json_schema(),
                },
            }
        )
        content = constrained.invoke(turns).content
        if not isinstance(content, str):
            raise RuntimeError(
                "structured boundary requires text content from the endpoint"
            )
        return content
