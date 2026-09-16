"""Repository-wide test configuration."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _tracing_is_off_unless_a_test_asks(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep every test untraced unless it is a real Langfuse integration test.

    Real-service suites load `.env`, where an operator may have switched tracing
    on for the host services. A test that happened to reach an entry point
    would then export its synthetic owner messages into the owner's trace store.
    """

    if request.node.get_closest_marker("langfuse") is None:
        monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "false")
