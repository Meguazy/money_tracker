"""Optional Langfuse integration for LangChain and LangGraph runs."""

from __future__ import annotations

import os

from langchain_core.callbacks.base import BaseCallbackHandler


def create_callbacks() -> list[BaseCallbackHandler]:
    """Create callbacks for the configured local Langfuse instance.

    Returns:
        A single Langfuse callback when both project credentials exist, or an
        empty list when tracing is not configured.
    """

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return []

    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def flush_traces(enabled: bool) -> None:
    """Deliver buffered traces before a short-lived process exits.

    Args:
        enabled: Whether Langfuse tracing was enabled for the current process.
    """

    if not enabled:
        return

    from langfuse import get_client

    get_client().flush()
