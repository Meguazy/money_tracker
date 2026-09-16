"""Fixtures shared by the real Langfuse tracing tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.integration.money_tracker.tracing.store import (
    ENVIRONMENT,
    LangfuseReader,
    required,
)


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> Iterator[LangfuseReader]:
    """Switch tracing on into the test environment and clean up afterwards."""

    reader = LangfuseReader(
        base_url=required("LANGFUSE_BASE_URL").rstrip("/"),
        auth=(required("LANGFUSE_PUBLIC_KEY"), required("LANGFUSE_SECRET_KEY")),
    )
    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", ENVIRONMENT)
    try:
        yield reader
    finally:
        reader.delete_created()
