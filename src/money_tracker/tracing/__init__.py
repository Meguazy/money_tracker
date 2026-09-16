"""Tracing boundary between the inference path and the local trace store.

Entry points call `open_tracer` once and inject the result into the model
adapter and the workflow dispatcher. Nothing below an entry point decides
whether to trace, and nothing below the Langfuse adapter imports a tracing SDK.

AD-021 records what traces contain and why: complete prompts, completions, and
decision context in a local store the owner controls, kept apart from the
operational journal, which still carries no owner content.
"""

from __future__ import annotations

from money_tracker.structured_logging import get_logger
from money_tracker.tracing.ports import (
    DISABLED_TRACER,
    DisabledTracer,
    ModelCallObservation,
    ModelCallRequest,
    TraceMessage,
    TraceObservation,
    Tracer,
)
from money_tracker.tracing.settings import (
    TracingConfigurationError,
    TracingSettings,
)

_LOGGER = get_logger(__name__)


def open_tracer(settings: TracingSettings | None = None) -> Tracer:
    """Build the tracer an entry point injects into the inference path.

    Tracing never decides whether an entry point can run. Unreadable or
    incomplete configuration is recorded and the process continues untraced,
    because AD-002 keeps core operation independent of the trace store.

    Args:
        settings: Explicit settings; read from the environment when omitted.

    Returns:
        The disabled tracer unless tracing is switched on and correctly
        configured, in which case a Langfuse tracer bound to the local store.
    """

    if settings is None:
        try:
            settings = TracingSettings.from_env()
        except TracingConfigurationError as error:
            _LOGGER.error(
                "tracing.configuration.rejected", reason_code=error.reason_code
            )
            return DISABLED_TRACER
    if not settings.enabled:
        _LOGGER.debug("tracing.disabled")
        return DISABLED_TRACER

    # Imported here so a disabled process never loads the SDK or registers an
    # OpenTelemetry provider.
    from money_tracker.tracing.identity import ModelIdentityResolver
    from money_tracker.tracing.langfuse import LangfuseTracer
    from money_tracker.tracing.redaction import TraceRedactor

    try:
        return LangfuseTracer(
            settings,
            identity=ModelIdentityResolver.from_env(),
            redactor=TraceRedactor.from_env(),
        )
    except Exception as error:
        _LOGGER.exception("tracing.start.failed", exception_type=type(error).__name__)
        return DISABLED_TRACER


__all__ = [
    "DISABLED_TRACER",
    "DisabledTracer",
    "ModelCallObservation",
    "ModelCallRequest",
    "TraceMessage",
    "TraceObservation",
    "Tracer",
    "TracingConfigurationError",
    "TracingSettings",
    "open_tracer",
]
