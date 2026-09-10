"""How the long-running host processes stop, and why.

AD-018 leaves health and recovery to operator commands and process exit codes
until the Step 5 control plane exists, so a supervisor has to be able to tell
two failures apart without reading logs. A missing credential and an
unreachable database both stop the process, but restarting helps only one of
them: the other would restart forever, hiding a problem the operator has to
fix. The systemd units use these codes to make exactly that distinction.

The same module owns the deliberate stop, because a planned shutdown is the
third way one of these processes ends and the units depend on it behaving
differently from either failure.
"""

from __future__ import annotations

import signal
from enum import IntEnum
from threading import Event
from types import FrameType

from langchain_core.exceptions import ModelConnectionError
from psycopg import Error as PsycopgError
from psycopg import OperationalError, ProgrammingError

from money_tracker.telegram.transport import TelegramTransportError


class ExitCode(IntEnum):
    """Why a host process stopped, in the form a supervisor can act on.

    Attributes:
        OK: The requested operation finished.
        CONFIGURATION: Something an operator must change first. Restarting
            without that change repeats the same failure.
        DEPENDENCY: A service the process needs was unreachable. Restarting is
            the correct response once it returns.
    """

    OK = 0
    CONFIGURATION = 1
    DEPENDENCY = 2


def exit_code_for(error: BaseException) -> ExitCode:
    """Classify one startup or runtime failure for a supervisor.

    Anything unrecognized is treated as a configuration problem, because a
    restart loop that hides an unknown defect is worse than a stopped service
    the operator can see.

    Args:
        error: Failure that ended the operation.

    Returns:
        The exit code the process should terminate with.
    """

    if isinstance(error, OperationalError | ModelConnectionError):
        return ExitCode.DEPENDENCY
    if isinstance(error, TelegramTransportError):
        return ExitCode.DEPENDENCY if error.transient else ExitCode.CONFIGURATION
    return ExitCode.CONFIGURATION


def failure_reason(error: BaseException) -> str:
    """Name a failure safely enough to print and log.

    Args:
        error: Failure that ended the operation.

    Returns:
        A stable code that names no credential, URL, or owner content.
    """

    if isinstance(error, OperationalError):
        return "database_unavailable"
    if isinstance(error, ProgrammingError):
        return "database_schema_invalid"
    if isinstance(error, PsycopgError):
        return "database_operation_invalid"
    if isinstance(error, ModelConnectionError):
        return "local_inference_unavailable"
    if isinstance(error, TelegramTransportError):
        return error.code
    return "configuration_invalid"


def shutdown_event() -> Event:
    """Install handlers that ask the caller's loop to stop between units.

    The units stop their services with SIGINT and systemd sends SIGTERM at the
    end of `TimeoutStopSec`. Python's default SIGINT handler raises
    `KeyboardInterrupt` wherever execution happens to be, which abandons a
    claimed work unit mid-flight and leaves its lease held until it expires: a
    planned stop then takes the same path as a crash, and the stop timeout has
    nothing to wait for. Setting an event instead lets the loop finish what it
    already claimed and return normally, so the process exits `ExitCode.OK`
    exactly as the operations table promises.

    Returns:
        An event set once the supervisor asks the process to stop.

    Raises:
        ValueError: If called from anywhere but the main thread, where Python
            forbids installing handlers.
    """

    stop = Event()

    def _request_stop(number: int, frame: FrameType | None) -> None:
        """Record the request without doing work inside a signal handler."""

        stop.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, _request_stop)
    return stop


__all__ = ["ExitCode", "exit_code_for", "failure_reason", "shutdown_event"]
