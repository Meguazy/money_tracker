"""Read and clean the real local Langfuse through its public API.

Shared by every real tracing test. It speaks the v4 events-only API this
Langfuse runs: observations are read from `/api/public/v2/observations`, and a
trace is removed with `DELETE /api/public/traces/{traceId}`, which Langfuse
applies asynchronously.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

ENVIRONMENT = "integration-test"
"""Langfuse environment every real tracing test writes to."""

VISIBILITY_SECONDS = 90.0
"""Upper bound on how long an exported observation takes to become readable."""

EXPANDED_METADATA = (
    "model_artifact",
    "request_parameters",
    "parse",
    "response",
    "router_prompt",
    "read_selection_policy",
    "extraction",
    "category_fit",
    "read_tool_request",
    "read_admitted_tools",
    "thread_ids",
    "revocation_outbox_ids",
)
"""Metadata keys whose complete values the tests read back."""


def required(name: str) -> str:
    """Read a setting the real services need, failing rather than skipping."""

    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for real Langfuse tests")
    return value


def named(observations: Iterable[dict[str, Any]], name: str) -> dict[str, Any]:
    """Return the single observation carrying one name."""

    items = list(observations)
    matches = [item for item in items if item["name"] == name]
    assert len(matches) == 1, sorted(item["name"] for item in items)
    return matches[0]


@dataclass
class LangfuseReader:
    """Read and delete observations through the real public API.

    Attributes:
        base_url: Local Langfuse base URL.
        auth: Project key pair.
        created: Trace identifiers this test created, deleted at teardown.
    """

    base_url: str
    auth: tuple[str, str] = field(repr=False)
    created: list[str] = field(default_factory=list)

    def observations(self, trace_id: str) -> list[dict[str, Any]]:
        """Return every observation of one trace with payloads and metadata."""

        response = httpx.get(
            f"{self.base_url}/api/public/v2/observations",
            params={
                "traceId": trace_id,
                "limit": 1000,
                "fields": "core,basic,io,metadata,model,usage,metrics",
                "expandMetadata": ",".join(EXPANDED_METADATA),
            },
            auth=self.auth,
            timeout=10.0,
        )
        response.raise_for_status()
        data: list[dict[str, Any]] = response.json()["data"]
        return data

    def wait_for(
        self, trace_id: str, ready: Callable[[list[dict[str, Any]]], bool]
    ) -> list[dict[str, Any]]:
        """Poll until the trace satisfies `ready`, within the ingestion bound."""

        deadline = time.monotonic() + VISIBILITY_SECONDS
        observed: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            observed = self.observations(trace_id)
            if ready(observed):
                return observed
            time.sleep(1.0)
        raise AssertionError(
            f"trace {trace_id} incomplete after {VISIBILITY_SECONDS}s: "
            f"{sorted(item['name'] for item in observed)}"
        )

    def matching_input(self, marker: str) -> list[dict[str, Any]]:
        """Find observations in the test environment whose input names a marker."""

        response = httpx.get(
            f"{self.base_url}/api/public/v2/observations",
            params={
                "environment": ENVIRONMENT,
                "limit": 50,
                "filter": json.dumps(
                    [
                        {
                            "type": "string",
                            "column": "input",
                            "operator": "matches",
                            "value": marker,
                        }
                    ]
                ),
            },
            auth=self.auth,
            timeout=10.0,
        )
        response.raise_for_status()
        data: list[dict[str, Any]] = response.json()["data"]
        return data

    def delete_created(self) -> None:
        """Request deletion of every trace this test created."""

        for trace_id in self.created:
            httpx.delete(
                f"{self.base_url}/api/public/traces/{trace_id}",
                auth=self.auth,
                timeout=10.0,
            )


def complete(names: set[str]) -> Callable[[list[dict[str, Any]]], bool]:
    """Build a readiness check requiring named, ended observations."""

    def ready(observations: list[dict[str, Any]]) -> bool:
        present = {item["name"] for item in observations}
        return names <= present and all(item.get("endTime") for item in observations)

    return ready
