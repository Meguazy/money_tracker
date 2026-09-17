"""Measure the traced gateway and worker in a process of their own.

The Langfuse SDK keeps one client per project key for the whole process and
ignores the endpoint of any later one, so a test cannot point a second tracer
at a failing store from inside pytest. This module runs the production gateway,
ingestor, worker, and dispatcher composition over the isolated database in a
fresh interpreter, alternating an untraced channel with one traced into the
store `LANGFUSE_BASE_URL` names, and prints its measurements as JSON.

Run with `python -m tests.integration.money_tracker.tracing.channel_harness`
from the repository root, with `RUN_POSTGRES_INTEGRATION_TESTS=1`, the test
database configured, and tracing switched on.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

from money_tracker.structured_logging import configure_logging
from money_tracker.tracing import DISABLED_TRACER, open_tracer
from tests.integration.money_tracker.conftest import (
    _isolated_test_url,
    _reset_test_database,
)
from tests.integration.money_tracker.tracing.test_channel_tracing import _channel

ROUNDS = int(os.getenv("CHANNEL_HARNESS_ROUNDS", "15"))
QUESTION = "Quanto ho speso con la carta questo mese?"


def main() -> None:
    """Run the alternating measurement and print the results."""

    configure_logging()
    database_url = _isolated_test_url()
    _reset_test_database(database_url)
    tracer = open_tracer()
    results: dict[str, object] = {"tracing_enabled": tracer.enabled}
    try:
        with _channel(database_url, DISABLED_TRACER) as plain:
            traced = plain.traced_with(tracer)
            durations: dict[str, list[float]] = {"untraced": [], "traced": []}
            answers: dict[str, list[str]] = {"untraced": [], "traced": []}
            for warm in (plain, traced):
                warm.exchange(warm.bot_api.text("/help"))
            for _ in range(ROUNDS):
                for name, channel in (("untraced", plain), ("traced", traced)):
                    started = time.perf_counter()
                    channel.exchange(channel.bot_api.text("/help"))
                    durations[name].append((time.perf_counter() - started) * 1000)
                    answers[name].append(channel.bot_api.sent[-1]["text"])
            started = time.perf_counter()
            traced.exchange(traced.bot_api.text(QUESTION))
            results["model_exchange_ms"] = round(
                (time.perf_counter() - started) * 1000, 1
            )
            health = plain.messaging.health(plain.bot_key)
            results["pending_inbox"] = health.pending_inbox
            results["retrying_inbox"] = health.retrying_inbox
            results["inbox_dead_letters"] = health.inbox_dead_letters
            results["pending_outbox"] = health.pending_outbox
            results["delivered_messages"] = len(plain.bot_api.sent)
        results["median_ms"] = {
            name: round(statistics.median(values), 2)
            for name, values in durations.items()
        }
        results["identical_answers"] = answers["untraced"] == answers["traced"]
    finally:
        started = time.perf_counter()
        tracer.shutdown()
        results["flush_ms"] = round((time.perf_counter() - started) * 1000, 1)
        _reset_test_database(database_url)
    sys.stdout.write(json.dumps(results) + "\n")


if __name__ == "__main__":
    main()
