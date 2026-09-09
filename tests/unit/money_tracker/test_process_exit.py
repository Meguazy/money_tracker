"""Behavior tests for the exit codes a supervisor acts on."""

from __future__ import annotations

import pytest
from langchain_core.exceptions import ModelConnectionError
from psycopg import OperationalError

from money_tracker.process_exit import ExitCode, exit_code_for, failure_reason
from money_tracker.telegram.transport import TelegramTransportError


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OperationalError("connection refused"), ExitCode.DEPENDENCY),
        (ModelConnectionError("llama.cpp is down"), ExitCode.DEPENDENCY),
        (
            TelegramTransportError("bot_api_unavailable", transient=True),
            ExitCode.DEPENDENCY,
        ),
        (
            TelegramTransportError("invalid_token", transient=False),
            ExitCode.CONFIGURATION,
        ),
        (
            RuntimeError("MONEY_TRACKER_TELEGRAM_ENVIRONMENT is unset"),
            ExitCode.CONFIGURATION,
        ),
        (ValueError("token file is world readable"), ExitCode.CONFIGURATION),
    ],
)
def test_a_supervisor_can_tell_the_two_failures_apart(
    error: Exception,
    expected: ExitCode,
) -> None:
    """Restart what a restart can fix, and stop for what it cannot."""

    assert exit_code_for(error) is expected


def test_an_unrecognized_failure_stops_instead_of_looping() -> None:
    """Prefer a visible stopped service over a hidden restart loop."""

    assert exit_code_for(KeyError("something new")) is ExitCode.CONFIGURATION


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            OperationalError("password authentication failed"),
            "database_unavailable",
        ),
        (ModelConnectionError("connection refused"), "local_inference_unavailable"),
        (TelegramTransportError("invalid_token", transient=False), "invalid_token"),
        (RuntimeError("token file /home/owner/secret.txt"), "configuration_invalid"),
    ],
)
def test_a_printed_reason_never_repeats_the_message(
    error: Exception,
    expected: str,
) -> None:
    """Name the failure without echoing a credential, path, or URL back out."""

    reason = failure_reason(error)

    assert reason == expected
    assert str(error) not in reason or not str(error)
