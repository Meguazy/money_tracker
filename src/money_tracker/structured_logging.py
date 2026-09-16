"""Structured application logging with a bound per-operation context.

Every process in this repository is a long-lived loop or a short command that
an owner runs by hand, and both end up in the same journal. A free-text line is
readable once and unusable afterwards: nothing can count how many work units
were dead-lettered, or follow one Telegram update through ingestion, dispatch,
persistence, and delivery.

So a log record here is a stable event name plus named fields, rendered as one
JSON object per line. The event name is machine-readable and never carries a
value; the values live in fields. That split is what makes the journal
queryable, and it is also what keeps owner content out of it: a field is added
deliberately, while an interpolated message tends to grow whatever the author
happened to have in scope.

Correlation comes from `log_context`, which binds identifiers for the duration
of one operation so that every record emitted underneath carries them without
being passed the values. The binding is a `contextvars` binding, so it follows
the operation rather than the process: concurrent gateway loops and worker
turns do not see each other's identifiers, and a thread that never entered the
block logs without them. A thread started by hand starts from an empty context,
which is why each long-running loop binds its own.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Generator, Mapping, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Final, TextIO

from money_tracker.logging_safety import (
    STRUCTURED_FIELDS_ATTRIBUTE,
    CredentialFilter,
    TracingSdkRecordFilter,
    quiet_http_loggers,
    quiet_tracing_sdk_loggers,
    redact_credentials,
)

JSON_FORMAT: Final = "json"
TEXT_FORMAT: Final = "text"
LOG_FORMATS: Final = (JSON_FORMAT, TEXT_FORMAT)
#: Keys the formatter writes itself. A field that collides with one is kept
#: under a suffixed name rather than dropped, because losing an observation is
#: worse than an awkward key.
RESERVED_KEYS: Final = frozenset({"timestamp", "level", "logger", "event", "error"})
_HANDLER_MARKER: Final = "money_tracker_structured_handler"
_EMPTY_CONTEXT: Final[Mapping[str, object]] = {}
_CONTEXT: ContextVar[Mapping[str, object]] = ContextVar(
    "money_tracker_log_context", default=_EMPTY_CONTEXT
)


def _merge(
    base: Mapping[str, object], extra: Mapping[str, object]
) -> dict[str, object]:
    """Combine two field mappings, renaming anything the formatter owns.

    Args:
        base: Lower-precedence fields, normally the bound context.
        extra: Higher-precedence fields, normally the call site's.

    Returns:
        One mapping in which no key shadows a reserved formatter key.
    """

    merged: dict[str, object] = {}
    for key, value in (*base.items(), *extra.items()):
        merged[f"{key}_field" if key in RESERVED_KEYS else key] = value
    return merged


def current_log_context() -> Mapping[str, object]:
    """Return the fields currently bound to this execution context.

    Returns:
        A read-only view of the bound identifiers, empty outside any
        `log_context` block.
    """

    return _CONTEXT.get()


@contextmanager
def log_context(**fields: object) -> Generator[None]:
    """Bind identifiers onto every record emitted inside the block.

    Nested blocks extend the enclosing binding instead of replacing it, and the
    previous binding is restored on exit even when the body raises, so a failed
    operation cannot leak its identifiers into whatever runs next.

    Args:
        **fields: Correlation identifiers such as `work_unit_id` or
            `profile_id`. Pass identifiers and classifications, never owner
            message text, receipt content, or a credential.

    Yields:
        None. The binding is ambient; nothing is passed to the body.
    """

    token = _CONTEXT.set({**_CONTEXT.get(), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


class StructuredLogger:
    """A logger whose records carry named fields instead of formatted text.

    Attributes:
        Instances are cheap wrappers over one standard library logger and are
        safe to create at module import; the underlying logger owns level and
        handler state, so a logger built before configuration still honors it.
    """

    __slots__ = ("_bound", "_logger")

    def __init__(
        self, logger: logging.Logger, bound: Mapping[str, object] | None = None
    ) -> None:
        """Wrap one standard library logger.

        Args:
            logger: Underlying logger, normally obtained from `get_logger`.
            bound: Fields repeated on every record this instance emits.
        """

        self._logger = logger
        self._bound = dict(bound or {})

    @property
    def name(self) -> str:
        """Name of the underlying standard library logger."""

        return self._logger.name

    def bind(self, **fields: object) -> StructuredLogger:
        """Derive a logger that repeats fields on every record.

        Use it for a value fixed for the lifetime of a component, such as the
        bot key a gateway serves. Per-operation identifiers belong in
        `log_context` instead, because they change faster than the component.

        Args:
            **fields: Fields added to every record of the derived logger.

        Returns:
            A new logger; this instance is unchanged.
        """

        return StructuredLogger(self._logger, {**self._bound, **fields})

    def debug(self, event: str, **fields: object) -> None:
        """Log an internal step useful only while diagnosing behavior."""

        self._emit(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: object) -> None:
        """Log a state change or completed unit of work worth keeping."""

        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: object) -> None:
        """Log a degraded or rejected outcome the system handled itself."""

        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: object) -> None:
        """Log a failure that needs an owner to look at it."""

        self._emit(logging.ERROR, event, fields)

    def exception(
        self, event: str, *, error: BaseException | None = None, **fields: object
    ) -> None:
        """Log a failure with the traceback of the exception it concerns.

        Args:
            event: Stable event name.
            error: Exception to attach. Defaults to the one currently being
                handled, so an `except` block passes nothing; a handler that
                runs outside the block, such as an exception hook, names it.
            **fields: Fields attached to the record.
        """

        self._emit(logging.ERROR, event, fields, exc_info=error or True)

    def critical(
        self, event: str, *, error: BaseException | None = None, **fields: object
    ) -> None:
        """Log a condition that ends the process or corrupts authority.

        Args:
            event: Stable event name.
            error: Exception to attach, when the condition has one.
            **fields: Fields attached to the record.
        """

        self._emit(logging.CRITICAL, event, fields, exc_info=error or False)

    @contextmanager
    def timed(
        self, event: str, **fields: object
    ) -> Generator[MutableMapping[str, Any]]:
        """Log the start, duration, and outcome of one bounded operation.

        The start is DEBUG because an operation that completes normally is
        described well enough by its completion record; the start matters only
        when something never finished. Completion is INFO with `duration_ms`,
        and an escaping exception is logged once at ERROR with its traceback
        and then re-raised, so the caller still decides what the failure means.

        Args:
            event: Stable event stem. The records append `.started`,
                `.completed`, and `.failed`.
            **fields: Fields attached to all three records.

        Yields:
            A mutable mapping for outcome fields the operation only learns
            while running, such as a row count. Anything put in it appears on
            the completion or failure record.
        """

        outcome: MutableMapping[str, Any] = {}
        self._emit(logging.DEBUG, f"{event}.started", fields)
        started = time.perf_counter()
        try:
            yield outcome
        except BaseException:
            self._emit(
                logging.ERROR,
                f"{event}.failed",
                {**fields, **outcome, "duration_ms": _elapsed_ms(started)},
                exc_info=True,
            )
            raise
        self._emit(
            logging.INFO,
            f"{event}.completed",
            {**fields, **outcome, "duration_ms": _elapsed_ms(started)},
        )

    def _emit(
        self,
        level: int,
        event: str,
        fields: Mapping[str, object],
        *,
        exc_info: bool | BaseException = False,
    ) -> None:
        """Send one record, skipping field assembly when the level is off."""

        if not self._logger.isEnabledFor(level):
            return
        merged = _merge(_merge(current_log_context(), self._bound), fields)
        self._logger.log(
            level,
            event,
            exc_info=exc_info,
            extra={STRUCTURED_FIELDS_ATTRIBUTE: merged},
            stacklevel=3,
        )


def _elapsed_ms(started: float) -> float:
    """Return milliseconds since a `perf_counter` reading, rounded for logs."""

    return round((time.perf_counter() - started) * 1000, 3)


def get_logger(name: str) -> StructuredLogger:
    """Return the structured logger for one module.

    Args:
        name: Dotted logger name, normally the module's `__name__` so that
            levels can be raised or lowered per package.

    Returns:
        A structured logger writing through the standard library hierarchy.
    """

    return StructuredLogger(logging.getLogger(name))


def _record_fields(record: logging.LogRecord) -> dict[str, object]:
    """Extract the structured fields a record carries, if any."""

    fields = getattr(record, STRUCTURED_FIELDS_ATTRIBUTE, None)
    return dict(fields) if isinstance(fields, dict) else {}


def _timestamp(record: logging.LogRecord) -> str:
    """Render a record's creation time as UTC ISO-8601 with milliseconds."""

    moment = datetime.fromtimestamp(record.created, tz=UTC)
    return f"{moment.isoformat(timespec='milliseconds').removesuffix('+00:00')}Z"


def _formatted_error(
    formatter: logging.Formatter, record: logging.LogRecord
) -> str | None:
    """Render a record's traceback with credentials removed, if it has one.

    Redaction happens here rather than in the filter because a traceback does
    not exist until a formatter builds it: an exception whose message or frame
    carried a token would otherwise reach the journal in full, with every other
    defence intact. `exc_info` is a falsy `False` rather than `None` on an
    ordinary record, so truthiness is the check that distinguishes them.

    Args:
        formatter: Formatter whose exception rendering is reused.
        record: Record being formatted.

    Returns:
        The redacted traceback, or None when the record carries no exception.
    """

    if not record.exc_info:
        return None
    return redact_credentials(formatter.formatException(record.exc_info))


class JsonFormatter(logging.Formatter):
    """Render one record as a single-line JSON object.

    Records from libraries that know nothing about this module still format
    correctly: they simply arrive with no fields, and their interpolated text
    becomes the event.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record, sorting keys so diffs and greps stay stable.

        Values that JSON cannot represent are rendered with `str`, because a
        log line must never fail to serialize; that covers the `UUID`,
        `Decimal`, and `datetime` values this codebase logs.
        """

        payload: dict[str, object] = _record_fields(record)
        payload.update(
            {
                "timestamp": _timestamp(record),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
            }
        )
        error = _formatted_error(self, record)
        if error is not None:
            payload["error"] = error
        return json.dumps(payload, default=str, sort_keys=True, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Render one record as an aligned line for a person reading a terminal.

    It carries the same fields as the JSON form in `key=value` pairs, so a
    session started for readability still shows every correlation identifier.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record as a timestamped event followed by its fields."""

        fields = _record_fields(record)
        rendered = " ".join(
            f"{key}={value}"
            for key, value in sorted(fields.items())
            if value is not None
        )
        line = (
            f"{_timestamp(record)} {record.levelname:<8} "
            f"{record.name} {record.getMessage()}"
        )
        if rendered:
            line = f"{line} {rendered}"
        error = _formatted_error(self, record)
        if error is not None:
            line = f"{line}\n{error}"
        return line


def _formatter(name: str) -> logging.Formatter:
    """Build the formatter for one configured output format.

    Args:
        name: Either `json` or `text`.

    Returns:
        The matching formatter.

    Raises:
        ValueError: If the name is not a supported format.
    """

    if name == JSON_FORMAT:
        return JsonFormatter()
    if name == TEXT_FORMAT:
        return TextFormatter()
    raise ValueError(
        f"LOG_FORMAT must be one of {', '.join(LOG_FORMATS)}, got {name!r}"
    )


def _level(value: str) -> int:
    """Resolve a configured level name to its numeric level.

    Args:
        value: Level name such as `INFO`, in any case.

    Returns:
        The numeric level.

    Raises:
        ValueError: If the name is not a standard logging level.
    """

    resolved = logging.getLevelNamesMapping().get(value.strip().upper())
    if resolved is None:
        raise ValueError(f"LOG_LEVEL must be a logging level name, got {value!r}")
    return resolved


def configure_logging(
    *,
    level: int | str | None = None,
    log_format: str | None = None,
    stream: TextIO | None = None,
    capture_uncaught: bool = True,
) -> None:
    """Configure process logging so no handler can emit a credential.

    Safe to call more than once: the handler this function installs is
    replaced rather than added, so a repeated call cannot double every line.
    Handlers installed by something else are left in place and given the
    redaction filter, which is what keeps a test harness's capture handler
    working and, more importantly, keeps redaction on records this module
    never sees.

    Args:
        level: Level for the application's own loggers. Defaults to `LOG_LEVEL`
            from the environment, then to INFO.
        log_format: Either `json` or `text`. Defaults to `LOG_FORMAT` from the
            environment, then to `json`.
        stream: Destination for the installed handler. Defaults to standard
            error, which keeps machine-readable command output on standard out
            usable in a pipeline.
        capture_uncaught: Whether to route uncaught exceptions, on the main
            thread and on any other, through logging. On by default so a crash
            is recorded and redacted like everything else.

    Raises:
        ValueError: If the configured level or format is not recognized.
    """

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if log_format is None:
        log_format = os.getenv("LOG_FORMAT", JSON_FORMAT)
    if stream is None:
        stream = sys.stderr
    resolved_level = _level(level) if isinstance(level, str) else level
    formatter = _formatter(log_format)
    credential_filter = CredentialFilter()
    sdk_filter = TracingSdkRecordFilter()
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARKER, False):
            root.removeHandler(existing)
        else:
            existing.addFilter(sdk_filter)
            existing.addFilter(credential_filter)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.addFilter(sdk_filter)
    handler.addFilter(credential_filter)
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
    root.setLevel(resolved_level)
    quiet_http_loggers()
    quiet_tracing_sdk_loggers(resolved_level)
    logging.captureWarnings(True)
    if capture_uncaught:
        _capture_uncaught_exceptions()


def _capture_uncaught_exceptions() -> None:
    """Route a crash through logging instead of the interpreter's own printer.

    An exception nobody catches is the failure most worth seeing, and by
    default it is the one least visible here: the interpreter prints it
    straight to standard error, which means it bypasses the formatter and,
    more seriously, the credential filter. A traceback that happens to carry a
    Bot API URL would reach the journal in full.

    `KeyboardInterrupt` keeps the default behavior, because a deliberate Ctrl-C
    is not a crash and should not be recorded as one.
    """

    logger = get_logger("money_tracker.process")

    def handle(
        exc_type: type[BaseException],
        error: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        """Record a crash on the main thread, or defer an interrupt."""

        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, error, traceback)
            return
        logger.critical(
            "process.unhandled_exception", error=error, exception=exc_type.__name__
        )

    def handle_thread(args: threading.ExceptHookArgs) -> None:
        """Record a crash on any other thread, naming the thread that died.

        A `SystemExit` is a thread ending deliberately, not a failure.
        """

        if args.exc_value is None or issubclass(args.exc_type, SystemExit):
            return
        logger.critical(
            "thread.unhandled_exception",
            error=args.exc_value,
            exception=args.exc_type.__name__,
            thread=None if args.thread is None else args.thread.name,
        )

    sys.excepthook = handle
    threading.excepthook = handle_thread


__all__ = [
    "JSON_FORMAT",
    "LOG_FORMATS",
    "RESERVED_KEYS",
    "TEXT_FORMAT",
    "JsonFormatter",
    "StructuredLogger",
    "TextFormatter",
    "configure_logging",
    "current_log_context",
    "get_logger",
    "log_context",
]
