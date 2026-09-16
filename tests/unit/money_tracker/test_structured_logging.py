"""Behavior tests for the structured logging contract."""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Iterator
from decimal import Decimal
from io import StringIO
from threading import Thread
from uuid import UUID

import pytest

from money_tracker.structured_logging import (
    configure_logging,
    current_log_context,
    get_logger,
    log_context,
)

TOKEN = "8435117429:AAF_-SEBbKbyAdP3FRUYCbJpk36Ypt5qXQo"


@pytest.fixture
def emitted() -> Iterator[StringIO]:
    """Configure logging onto a buffer and restore the root logger after.

    Yields:
        The buffer every configured handler writes to.
    """

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    previous_hook = sys.excepthook
    previous_thread_hook = threading.excepthook
    stream = StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=stream)
    try:
        yield stream
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)
        sys.excepthook = previous_hook
        threading.excepthook = previous_thread_hook


def _records(stream: StringIO) -> list[dict[str, object]]:
    """Parse the emitted buffer into one mapping per log line."""

    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_a_record_is_one_json_object_with_its_fields(emitted: StringIO) -> None:
    """Keep the event name machine-readable and the values in named fields."""

    get_logger("money_tracker.test").info("expense.recorded", allocations=2)

    record = _records(emitted)[0]

    assert record["event"] == "expense.recorded"
    assert record["level"] == "INFO"
    assert record["logger"] == "money_tracker.test"
    assert record["allocations"] == 2
    assert str(record["timestamp"]).endswith("Z")


def test_bound_context_reaches_records_it_never_touched(emitted: StringIO) -> None:
    """Correlate a whole operation without threading identifiers through it."""

    logger = get_logger("money_tracker.test")

    with log_context(work_unit_id="w-1"):
        with log_context(profile_id="p-1"):
            logger.info("work_unit.claimed")
        logger.info("work_unit.released")
    logger.info("worker.idle")

    claimed, released, idle = _records(emitted)
    assert claimed["work_unit_id"] == "w-1"
    assert claimed["profile_id"] == "p-1"
    assert released["work_unit_id"] == "w-1"
    assert "profile_id" not in released
    assert "work_unit_id" not in idle


def test_context_is_restored_when_the_operation_raises() -> None:
    """Stop a failed operation from leaking identifiers into the next one."""

    with pytest.raises(RuntimeError), log_context(work_unit_id="w-1"):
        raise RuntimeError("boom")

    assert current_log_context() == {}


def test_context_does_not_escape_into_another_thread(emitted: StringIO) -> None:
    """Keep concurrent gateway loops from borrowing each other's identifiers.

    A thread starts from an empty context, which is exactly why every
    long-running loop binds its own rather than relying on the caller's.
    """

    logger = get_logger("money_tracker.test")

    with log_context(work_unit_id="w-1"):
        thread = Thread(target=lambda: logger.info("outbox.polled"))
        thread.start()
        thread.join()

    assert "work_unit_id" not in _records(emitted)[0]


def test_bound_logger_repeats_component_fields(emitted: StringIO) -> None:
    """Attach a component-lifetime value without restating it per call."""

    logger = get_logger("money_tracker.test").bind(bot_key="production")

    logger.info("gateway.ready")
    get_logger("money_tracker.test").info("unbound.event")

    ready, unbound = _records(emitted)
    assert ready["bot_key"] == "production"
    assert "bot_key" not in unbound


def test_a_field_never_overwrites_a_formatter_key(emitted: StringIO) -> None:
    """Keep an awkwardly named observation instead of losing it silently."""

    get_logger("money_tracker.test").warning("outbox.retry", level="collision")

    record = _records(emitted)[0]
    assert record["level"] == "WARNING"
    assert record["level_field"] == "collision"


def test_domain_values_serialize_instead_of_failing(emitted: StringIO) -> None:
    """Never drop a line because a UUID or Decimal is not JSON native."""

    get_logger("money_tracker.test").info(
        "expense.recorded",
        expense_id=UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
        amount=Decimal("12.34"),
    )

    record = _records(emitted)[0]
    assert record["expense_id"] == "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    assert record["amount"] == "12.34"


def test_timed_reports_start_completion_and_duration(emitted: StringIO) -> None:
    """Describe a bounded operation with a quiet start and a loud outcome."""

    logger = get_logger("money_tracker.test")

    with logger.timed("workflow.dispatch", route="expense") as outcome:
        outcome["movements"] = 2

    started, completed = _records(emitted)
    assert started["event"] == "workflow.dispatch.started"
    assert started["level"] == "DEBUG"
    assert completed["event"] == "workflow.dispatch.completed"
    assert completed["level"] == "INFO"
    assert completed["route"] == "expense"
    assert completed["movements"] == 2
    assert isinstance(completed["duration_ms"], float)


def test_timed_logs_a_failure_once_and_re_raises(emitted: StringIO) -> None:
    """Leave the meaning of a failure to the caller while still recording it."""

    logger = get_logger("money_tracker.test")

    with pytest.raises(RuntimeError):  # noqa: PT012 - the block is the subject
        with logger.timed("workflow.dispatch"):
            raise RuntimeError("boom")

    _, failed = _records(emitted)
    assert failed["event"] == "workflow.dispatch.failed"
    assert failed["level"] == "ERROR"
    assert "RuntimeError: boom" in str(failed["error"])


def test_a_credential_in_a_field_is_redacted(emitted: StringIO) -> None:
    """Apply the same defence to structured fields as to a message."""

    get_logger("money_tracker.test").error("transport.failed", url=f"bot{TOKEN}/getMe")

    record = _records(emitted)[0]
    assert TOKEN not in json.dumps(record)
    assert record["url"] == "bot<redacted>/getMe"


def test_a_credential_in_a_traceback_is_redacted(emitted: StringIO) -> None:
    """Close the gap a filter cannot see, because a traceback is built later."""

    logger = get_logger("money_tracker.test")

    try:
        raise RuntimeError(f"call to bot{TOKEN}/getMe failed")
    except RuntimeError:
        logger.exception("transport.failed")

    assert TOKEN not in emitted.getvalue()


def test_records_from_other_libraries_still_format(emitted: StringIO) -> None:
    """Keep a dependency's plain record readable in the same stream."""

    logging.getLogger("alembic.runtime").info("Running upgrade -> %s", "0005")

    record = _records(emitted)[0]
    assert record["event"] == "Running upgrade -> 0005"
    assert record["logger"] == "alembic.runtime"


def test_reconfiguration_does_not_duplicate_a_line(emitted: StringIO) -> None:
    """Let an entry point configure logging twice without doubling output."""

    configure_logging(level="DEBUG", log_format="json", stream=emitted)
    get_logger("money_tracker.test").info("worker.started")

    assert len(_records(emitted)) == 1


def test_the_text_format_carries_the_same_fields() -> None:
    """Keep correlation identifiers visible when a person reads a terminal."""

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    stream = StringIO()
    try:
        configure_logging(
            level="INFO", log_format="text", stream=stream, capture_uncaught=False
        )
        with log_context(work_unit_id="w-1"):
            get_logger("money_tracker.test").info("work_unit.claimed", attempt=1)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)

    line = stream.getvalue().strip()
    assert "work_unit.claimed" in line
    assert "attempt=1" in line
    assert "work_unit_id=w-1" in line


def test_an_unusable_configuration_is_named_not_guessed() -> None:
    """Fail loudly on a typo rather than silently logging at the wrong level."""

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        configure_logging(level="verbose", capture_uncaught=False)
    with pytest.raises(ValueError, match="LOG_FORMAT"):
        configure_logging(level="INFO", log_format="logfmt", capture_uncaught=False)


def test_an_uncaught_exception_is_recorded_instead_of_printed(
    emitted: StringIO,
) -> None:
    """See the failure nobody caught, redacted like every other record.

    The interpreter would otherwise print it straight to standard error, past
    the formatter and past the credential filter.
    """

    error = RuntimeError(f"call to bot{TOKEN}/getMe failed")

    sys.excepthook(RuntimeError, error, error.__traceback__)

    record = _records(emitted)[0]
    assert record["event"] == "process.unhandled_exception"
    assert record["level"] == "CRITICAL"
    assert record["exception"] == "RuntimeError"
    assert TOKEN not in json.dumps(record)


def test_a_deliberate_interrupt_is_not_recorded_as_a_crash(
    emitted: StringIO,
) -> None:
    """Leave Ctrl-C alone: stopping a loop on purpose is not a failure."""

    error = KeyboardInterrupt()

    sys.excepthook(KeyboardInterrupt, error, error.__traceback__)

    assert _records(emitted) == []


def test_a_thread_that_dies_is_recorded_with_its_name(emitted: StringIO) -> None:
    """Surface a dead gateway loop, which otherwise fails in silence."""

    def boom() -> None:
        raise RuntimeError("loop died")

    thread = Thread(target=boom, name="money-tracker-telegram-0")
    thread.start()
    thread.join()

    record = _records(emitted)[0]
    assert record["event"] == "thread.unhandled_exception"
    assert record["level"] == "CRITICAL"
    assert record["thread"] == "money-tracker-telegram-0"
    assert "RuntimeError: loop died" in str(record["error"])


def test_tracing_sdk_debug_records_never_form(emitted: StringIO) -> None:
    """Keep a traced payload out of the journal even at DEBUG.

    The Langfuse callback logs complete chain inputs at DEBUG, which is exactly
    the owner content the journal must not hold.
    """

    logging.getLogger("langfuse").debug("on_chain_start inputs=%s", "Ho speso 12 euro")

    assert _records(emitted) == []


def test_tracing_sdk_records_become_content_free_events(emitted: StringIO) -> None:
    """Keep that tracing degraded, drop the URL, error text, and traceback."""

    exporter = logging.getLogger(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    )
    try:
        raise ConnectionError("http://localhost:3000/api/public/otel/v1/traces refused")
    except ConnectionError:
        exporter.error(
            "Failed to export span batch code: %s, reason: %s",
            None,
            "http://localhost:3000/api/public/otel/v1/traces",
            exc_info=True,
        )
    logging.getLogger("langfuse").warning("Masking error: %s", "Ho speso 12 euro")

    records = _records(emitted)

    assert [record["event"] for record in records] == [
        "tracing.export.degraded",
        "tracing.sdk.reported",
    ]
    assert records[0]["exception_type"] == "ConnectionError"
    assert records[0]["level"] == "WARNING"
    assert "error" not in records[0]
    assert "localhost" not in json.dumps(records)
    assert "Ho speso" not in json.dumps(records)


def test_a_strict_process_level_also_silences_tracing_sdk_warnings() -> None:
    """Never let the SDK loggers be noisier than the process asked for."""

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    stream = StringIO()
    configure_logging(level="CRITICAL", stream=stream, capture_uncaught=False)
    try:
        logging.getLogger("opentelemetry.sdk").warning("Exporter failed")
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)

    assert stream.getvalue() == ""
