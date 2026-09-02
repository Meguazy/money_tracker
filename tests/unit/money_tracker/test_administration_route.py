"""Tests for the local routing administration command."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.exceptions import ModelConnectionError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from money_tracker.administration import main
from money_tracker.workflows import (
    RoutingReason,
    RoutingSource,
    SupportedCommand,
    WorkflowIntent,
)


class StubEndpoint(BaseChatModel):
    """A minimal real chat model standing in for the local endpoint."""

    completions: list[str] = Field(default_factory=list)
    calls: list[int] = Field(default_factory=list)
    fail_with_connection_error: bool = False

    @property
    def _llm_type(self) -> str:
        """Identify this model implementation to LangChain."""

        return "stub-endpoint"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Replay one completion or simulate an unreachable endpoint."""

        if self.fail_with_connection_error:
            raise ModelConnectionError("endpoint unreachable")
        self.calls.append(1)
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content=self.completions[len(self.calls) - 1])
                )
            ]
        )


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> StubEndpoint:
    """Install a stub endpoint behind the real adapter construction path."""

    stub = StubEndpoint()
    monkeypatch.setattr("money_tracker.runtime.model.create_chat_model", lambda: stub)
    return stub


def _run(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    """Run the command through its real entry point and parse its JSON."""

    main(argv)
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    return payload


def _classification(intent: str, confidence: float = 0.95) -> str:
    """Render one schema-valid classification completion."""

    return json.dumps({"intent": intent, "confidence": confidence, "evidence": "testo"})


def test_image_routes_deterministically_without_the_endpoint(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let an owner exercise the deterministic half with no model running."""

    payload = _run(["route", "--with-image", "--minimum-confidence", "0.8"], capsys)

    assert endpoint.calls == []
    assert payload["intent"] == WorkflowIntent.RECEIPT.value
    assert payload["source"] == RoutingSource.DETERMINISTIC.value
    assert payload["reason"] == RoutingReason.IMAGE_ATTACHMENT.value


def test_supported_command_routes_without_the_endpoint(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report the recognized command through the real entry point."""

    payload = _run(
        ["route", "--text", "/status", "--minimum-confidence", "0.8"], capsys
    )

    assert endpoint.calls == []
    assert payload["intent"] == WorkflowIntent.COMMAND.value
    assert payload["command"] == SupportedCommand.STATUS.value


def test_free_text_reaches_the_endpoint_and_reports_the_decision(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Classify real Italian prose through the router and the adapter."""

    endpoint.completions = [_classification("expense", 0.93)]

    payload = _run(
        [
            "route",
            "--text",
            "Ho speso 12 euro per i croccantini dei gatti",
            "--minimum-confidence",
            "0.8",
        ],
        capsys,
    )

    assert endpoint.calls == [1]
    assert payload["intent"] == WorkflowIntent.EXPENSE.value
    assert payload["source"] == RoutingSource.MODEL.value
    assert payload["confidence"] == 0.93


def test_output_omits_the_owner_message(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep the printed decision free of message content, as the contract is."""

    endpoint.completions = [_classification("expense")]
    secret = "Ho speso 12 euro dal veterinario per il gatto"

    main(["route", "--text", secret, "--minimum-confidence", "0.8"])
    output = capsys.readouterr().out

    assert secret not in output
    assert "veterinario" not in output


def test_supplied_context_makes_a_follow_up_reachable(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let an owner reach a correlated route without an active profile."""

    pending = uuid4()
    endpoint.completions = [_classification("category_follow_up")]

    payload = _run(
        [
            "route",
            "--text",
            "usa la categoria Animali",
            "--minimum-confidence",
            "0.8",
            "--pending-review-id",
            str(pending),
        ],
        capsys,
    )

    assert payload["intent"] == WorkflowIntent.CATEGORY_FOLLOW_UP.value
    assert payload["pending_action_id"] == str(pending)


def test_absent_context_clarifies_instead_of_accepting(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Show the policy veto by hand when no review is pending."""

    endpoint.completions = [_classification("category_follow_up")]

    payload = _run(
        [
            "route",
            "--text",
            "usa la categoria Animali",
            "--minimum-confidence",
            "0.8",
        ],
        capsys,
    )

    assert payload["intent"] == WorkflowIntent.CLARIFICATION.value
    assert payload["reason"] == RoutingReason.NO_PENDING_REVIEW.value


def test_supplied_movement_makes_an_undo_reachable(
    endpoint: StubEndpoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let an owner exercise the undo route against a stated movement."""

    movement = uuid4()
    endpoint.completions = [_classification("undo")]

    payload = _run(
        [
            "route",
            "--text",
            "annulla l'ultima spesa",
            "--minimum-confidence",
            "0.8",
            "--latest-movement-id",
            str(movement),
        ],
        capsys,
    )

    assert payload["intent"] == WorkflowIntent.UNDO.value
    assert payload["target_movement_id"] == str(movement)


def test_confidence_bar_is_required(endpoint: StubEndpoint) -> None:
    """Refuse to imply an uncalibrated production threshold."""

    with pytest.raises(SystemExit):
        main(["route", "--text", "ho speso 12 euro"])


@pytest.mark.parametrize("value", ["-0.1", "1.5"])
def test_confidence_bar_outside_the_unit_interval_is_refused(
    endpoint: StubEndpoint,
    value: str,
) -> None:
    """Reject a nonsensical bar before spending an inference call."""

    with pytest.raises(SystemExit, match="between 0 and 1"):
        main(["route", "--text", "ho speso", "--minimum-confidence", value])
    assert endpoint.calls == []


def test_message_with_nothing_routable_is_refused(
    endpoint: StubEndpoint,
) -> None:
    """Explain an empty invocation instead of raising a contract error."""

    with pytest.raises(SystemExit, match="nothing to route"):
        main(["route", "--minimum-confidence", "0.8"])


def test_unreachable_endpoint_names_the_missing_service(
    endpoint: StubEndpoint,
) -> None:
    """Point at the server to start rather than surfacing a transport error."""

    endpoint.fail_with_connection_error = True

    with pytest.raises(SystemExit, match="run_llama_server"):
        main(
            [
                "route",
                "--text",
                "ho speso 12 euro",
                "--minimum-confidence",
                "0.8",
            ]
        )
