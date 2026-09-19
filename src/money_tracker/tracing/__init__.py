"""Tracing boundary between the inference path and the local trace store.

Entry points call `open_tracer` once and inject the result into the model
adapter and the workflow dispatcher. Nothing below an entry point decides
whether to trace, and nothing below the Langfuse adapter imports a tracing SDK.

AD-021 records what traces contain and why: complete prompts, completions, and
decision context in a local store the owner controls, kept apart from the
operational journal, which still carries no owner content.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping

from money_tracker.structured_logging import get_logger
from money_tracker.tracing.ports import (
    ADMIN_ROUTE_TRACE,
    DISABLED_TRACER,
    TELEGRAM_DELIVERY_STAGE,
    TELEGRAM_INGEST_STAGE,
    TELEGRAM_UPDATE_TRACE,
    WEB_ADVICE_TRACE,
    WORK_UNIT_STAGE,
    DisabledTracer,
    EvaluationPublication,
    EvaluationPublisher,
    EvaluationRecord,
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


def open_tracer(
    settings: TracingSettings | None = None,
    *,
    identity_in_background: bool = False,
) -> Tracer:
    """Build the tracer an entry point injects into the inference path.

    Tracing never decides whether an entry point can run. Unreadable or
    incomplete configuration is recorded and the process continues untraced,
    because AD-002 keeps core operation independent of the trace store.

    Args:
        settings: Explicit settings; read from the environment when omitted.
        identity_in_background: Resolve the serving model's identity on a
            background thread starting now, and never make a model call wait
            for an artifact digest. Long-running services that call the model
            set this; a one-shot command leaves it off so its only call names
            a complete identity.

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
        identity = ModelIdentityResolver.from_env(
            background_digests=identity_in_background
        )
        tracer = LangfuseTracer(
            settings,
            identity=identity,
            redactor=TraceRedactor.from_env(),
        )
    except Exception as error:
        _LOGGER.exception("tracing.start.failed", exception_type=type(error).__name__)
        return DISABLED_TRACER
    if identity_in_background:
        threading.Thread(
            target=_prewarm_identity,
            args=(identity,),
            name="money-tracker-identity-prewarm",
            daemon=True,
        ).start()
    return tracer


def open_evaluation_publisher(*, environment: str) -> EvaluationPublisher | None:
    """Build the publisher an evaluation command uses for aggregate evidence.

    The command's own flag or subcommand is the explicit request, so the
    runtime tracing switch is not consulted; the keys, loopback endpoint, and
    export bound are validated exactly as for tracing, and the SDK's cloud
    default is never used.

    Args:
        environment: Langfuse environment the evidence is published into.

    Returns:
        A publisher bound to the local store, or None when configuration
        cannot be honored, which is recorded with a reason code.
    """

    try:
        settings = TracingSettings.for_command(environment=environment)
    except TracingConfigurationError as error:
        _LOGGER.error("tracing.configuration.rejected", reason_code=error.reason_code)
        return None

    from money_tracker.tracing.langfuse import LangfuseTracer
    from money_tracker.tracing.redaction import TraceRedactor

    try:
        return LangfuseTracer(
            settings,
            identity=_no_model_identity,
            redactor=TraceRedactor.from_env(),
        )
    except Exception as error:
        _LOGGER.exception("tracing.start.failed", exception_type=type(error).__name__)
        return None


def _no_model_identity() -> Mapping[str, object]:
    """Report no model identity; a publisher records no model call."""

    return {"metadata_complete": False}


def _prewarm_identity(identity: Callable[[], Mapping[str, object]]) -> None:
    """Resolve the model identity once so the first traced call finds it ready.

    A server that is not up yet is not a fault here: the first model call reads
    the report again, and the call would fail on the endpoint anyway.

    Args:
        identity: Resolver whose first call reads `/props` and starts any
            digest measurement.
    """

    try:
        resolved = identity()
    except Exception as error:
        _LOGGER.warning(
            "tracing.identity.prewarm_failed", exception_type=type(error).__name__
        )
        return
    _LOGGER.debug(
        "tracing.identity.prewarmed",
        metadata_complete=resolved.get("metadata_complete") is True,
        digest_pending=resolved.get("digest_pending") is True,
    )


__all__ = [
    "ADMIN_ROUTE_TRACE",
    "DISABLED_TRACER",
    "TELEGRAM_DELIVERY_STAGE",
    "TELEGRAM_INGEST_STAGE",
    "TELEGRAM_UPDATE_TRACE",
    "WORK_UNIT_STAGE",
    "WEB_ADVICE_TRACE",
    "DisabledTracer",
    "EvaluationPublication",
    "EvaluationPublisher",
    "EvaluationRecord",
    "ModelCallObservation",
    "ModelCallRequest",
    "TraceMessage",
    "TraceObservation",
    "Tracer",
    "TracingConfigurationError",
    "TracingSettings",
    "open_evaluation_publisher",
    "open_tracer",
]
