"""Strict-schema model boundary with exactly one corrective retry.

AD-006 fixes the retry budget: structured-output parsing gets one corrective
retry and then routes to clarification instead of looping. That budget is
expressed here as straight-line code rather than a loop, so the boundary
structurally cannot retry twice however it is later called.

The model is a narrow port. All parsing, repair, and failure policy is
deterministic code that a fake port exercises completely, which keeps the
"schema valid after at most one corrective retry" gate testable without an
inference server.

Failures report only how many attempts were consumed. Neither the rejected
output nor the owner message enters an exception message, because those reach
logs and traces while the repair turn does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ValidationError

MODEL_BOUNDARY_ATTEMPTS = 2
"""One initial attempt plus the single corrective retry AD-006 permits."""

MAX_REPAIRED_OUTPUT_CHARACTERS = 2000
"""Cap on rejected output quoted back to the model.

A runaway completion must not enlarge the repair turn without bound, because
the repair has to fit the same context window as the original request.
"""


@dataclass(frozen=True, slots=True)
class RepairRequest:
    """The rejected output and why it failed, for one corrective retry.

    Attributes:
        invalid_output: Prior completion, truncated to the quoting cap.
        validation_error: Field paths and messages describing the rejection.
            Rejected input values are deliberately excluded, so the repair turn
            introduces no untrusted content beyond the prior completion itself.
    """

    invalid_output: str
    validation_error: str


class StructuredOutputModel(Protocol):
    """Narrow port producing one strict-schema JSON completion.

    Implementations own prompt assembly, transport, and the provider's
    structured-output mechanism. They own no parsing, retry, or failure policy.
    """

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: RepairRequest | None = None,
    ) -> str:
        """Return raw model output expected to satisfy the schema.

        Args:
            instructions: Task contract for this boundary.
            message: Untrusted owner text to interpret.
            schema: Contract the completion must satisfy.
            repair: Prior rejection to correct, on the single retry only.

        Returns:
            Raw completion text, parsed by the caller.
        """


class StructuredOutputError(RuntimeError):
    """Raised when a model cannot satisfy a schema within the retry budget.

    Attributes:
        attempts: How many attempts were consumed before giving up.
    """

    def __init__(self, attempts: int) -> None:
        """Bind the failure to its attempt count and nothing else.

        Args:
            attempts: How many attempts were consumed.
        """

        super().__init__(f"structured output invalid after {attempts} attempts")
        self.attempts = attempts


def summarize_validation_error(error: ValidationError) -> str:
    """Describe a rejection by field path and message only.

    Rejected input values are excluded so that the repair turn adds no owner
    text of its own, and so the summary stays small enough to prepend to a
    bounded context.

    Args:
        error: Rejection raised while parsing a completion.

    Returns:
        Semicolon-separated `path: message` pairs.
    """

    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(root)'}: {item['msg']}"
        for item in error.errors(include_url=False)
    )


def parse_with_one_repair[ContractT: BaseModel](
    model: StructuredOutputModel,
    *,
    schema: type[ContractT],
    instructions: str,
    message: str,
) -> ContractT:
    """Parse one completion, allowing exactly one corrective retry.

    Args:
        model: Narrow structured-output port.
        schema: Contract the completion must satisfy.
        instructions: Task contract for this boundary.
        message: Untrusted owner text to interpret.

    Returns:
        The validated contract instance.

    Raises:
        StructuredOutputError: If the repaired completion is still invalid.
            The caller answers this with clarification, never a further retry.
    """

    first = model.complete_json(
        instructions=instructions, message=message, schema=schema
    )
    try:
        return schema.model_validate_json(first)
    except ValidationError as first_error:
        repair = RepairRequest(
            invalid_output=first[:MAX_REPAIRED_OUTPUT_CHARACTERS],
            validation_error=summarize_validation_error(first_error),
        )
    repaired = model.complete_json(
        instructions=instructions,
        message=message,
        schema=schema,
        repair=repair,
    )
    try:
        return schema.model_validate_json(repaired)
    except ValidationError as second_error:
        raise StructuredOutputError(MODEL_BOUNDARY_ATTEMPTS) from second_error
