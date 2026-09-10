"""Behavior tests for the exit codes a supervisor acts on."""

from __future__ import annotations

import os
import signal

import pytest
from langchain_core.exceptions import ModelConnectionError
from psycopg import DataError, OperationalError, ProgrammingError

from money_tracker.process_exit import (
    ExitCode,
    exit_code_for,
    failure_reason,
    shutdown_event,
)
from money_tracker.telegram.transport import TelegramTransportError


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OperationalError("connection refused"), ExitCode.DEPENDENCY),
        (ProgrammingError("relation is missing"), ExitCode.CONFIGURATION),
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
        (ProgrammingError("relation contains private name"), "database_schema_invalid"),
        (DataError("row contains private value"), "database_operation_invalid"),
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


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_a_stop_signal_is_recorded_instead_of_interrupting(number: int) -> None:
    """Turn a supervisor's stop request into a request the loop can finish on.

    The default SIGINT disposition raises `KeyboardInterrupt` wherever the
    interpreter happens to be, which is how a planned stop used to abandon a
    claimed work unit and leave its lease held. The default SIGTERM disposition
    kills the process outright, so reaching the final assertion at all proves a
    handler is installed.
    """

    previous = {
        received: signal.getsignal(received)
        for received in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        stop = shutdown_event()
        assert not stop.is_set()

        os.kill(os.getpid(), number)

        assert stop.wait(5)
    finally:
        for received, handler in previous.items():
            signal.signal(received, handler)
