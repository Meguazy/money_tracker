"""Optional Langfuse integration for LangChain and LangGraph runs."""

from __future__ import annotations

import os

from langchain_core.callbacks.base import BaseCallbackHandler

from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.observability")


def create_callbacks() -> list[BaseCallbackHandler]:
    """Create callbacks for the configured local Langfuse instance.

    Returns:
        A single Langfuse callback when both project credentials exist, or an
        empty list when tracing is not configured.
    """

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        # Tracing is optional by design, so its absence is a fact to record
        # rather than a problem: it explains an empty Langfuse project later.
        _LOGGER.debug("tracing.disabled")
        return []

    from langfuse.langchain import CallbackHandler

    _LOGGER.info("tracing.enabled")
    return [CallbackHandler()]


def flush_traces(enabled: bool) -> None:
    """Deliver buffered traces before a short-lived process exits.

    Args:
        enabled: Whether Langfuse tracing was enabled for the current process.
    """

    if not enabled:
        return

    from langfuse import get_client

    _LOGGER.debug("tracing.flushing")
    get_client().flush()
