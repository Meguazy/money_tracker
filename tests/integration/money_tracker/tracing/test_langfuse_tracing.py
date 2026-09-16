"""Real Langfuse and llama.cpp tests for traced dispatch.

Every test drives the production `money-tracker-admin route` entry point, lets
the real SDK export to the running local Langfuse, and reads the result back
through Langfuse's public API. Nothing here mocks the SDK, the exporter, the
store, or the model endpoint.

The traces carry a dedicated `integration-test` environment, and each test
deletes the traces it created. Deletion in Langfuse is asynchronous, so the
tests request it and do not wait for it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

import httpx
import pytest

from money_tracker.administration import main
from money_tracker.domain import BalanceMode, FundingClass
from money_tracker.persistence.database import bootstrap_checkpointer, upgrade_database
from money_tracker.tracing.langfuse import trace_id_for
from money_tracker.workflows import ROUTER, default_prompt
from tests.integration.money_tracker.tracing.store import (
    VISIBILITY_SECONDS,
    LangfuseReader,
    named,
    required,
)

pytestmark = [
    pytest.mark.langfuse,
    pytest.mark.inference,
    pytest.mark.skipif(
        os.getenv("RUN_LANGFUSE_INTEGRATION_TESTS") != "1",
        reason=(
            "set RUN_LANGFUSE_INTEGRATION_TESTS=1 with the observability profile "
            "and the local model running"
        ),
    ),
]

EXPENSE_TEXT = "Ho speso 12 euro per i croccantini dei gatti"


def _route(
    capsys: pytest.CaptureFixture[str],
    reader: LangfuseReader,
    *arguments: str,
    text: str = EXPENSE_TEXT,
) -> tuple[str, dict[str, Any]]:
    """Run one routing command under a fresh source reference."""

    reference = f"integration-test:tracing:{uuid4()}"
    reader.created.append(trace_id_for(reference))
    main(
        [
            "route",
            "--text",
            text,
            "--minimum-confidence",
            "0.5",
            "--source-reference",
            reference,
            *arguments,
        ]
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    return trace_id_for(reference), payload


def _complete_router_trace(observations: list[dict[str, Any]]) -> bool:
    """Tell whether the routing trace has reached the store in full."""

    names = {item["name"] for item in observations}
    return {"work-unit", "dispatch", "graph:router", "model:RouteClassification"} <= (
        names
    ) and all(item.get("endTime") for item in observations)


def test_a_routed_message_exports_its_complete_model_call(
    langfuse: LangfuseReader, capsys: pytest.CaptureFixture[str]
) -> None:
    """Record prompt, message, schema, completion, contract, identity, and cost."""

    trace_id, payload = _route(capsys, langfuse)

    observations = langfuse.wait_for(trace_id, _complete_router_trace)

    root = named(observations, "work-unit")
    dispatch = named(observations, "dispatch")
    router = named(observations, "graph:router")
    classify = named(observations, "classify")
    generation = named(observations, "model:RouteClassification")
    by_id = {item["id"]: item for item in observations}
    assert by_id[dispatch["parentObservationId"]]["name"] == "work-unit"
    assert by_id[router["parentObservationId"]]["name"] == "dispatch"
    assert by_id[generation["parentObservationId"]]["name"] == "classify"
    assert classify["latency"] >= generation["latency"] > 0
    assert json.loads(root["output"]) == payload

    work_unit = json.loads(dispatch["input"])
    assert work_unit["message"]["text"] == EXPENSE_TEXT
    assert dispatch["metadata"]["router_prompt"] == default_prompt(ROUTER).id
    assert dispatch["metadata"]["routing_minimum_confidence"] == 0.5

    request = json.loads(generation["input"])
    assert request["messages"] == [
        {"role": "system", "content": default_prompt(ROUTER).text},
        {"role": "user", "content": EXPENSE_TEXT},
    ]
    assert request["response_format"]["json_schema"]["name"] == "RouteClassification"
    assert request["response_format"]["json_schema"]["strict"] is True
    completion = generation["output"]
    assert json.loads(completion)["intent"] == payload["intent"]
    parse = generation["metadata"]["parse"]
    assert parse["valid"] is True
    assert parse["contract"]["intent"] == payload["intent"]
    assert generation["usageDetails"]["input"] > 0
    assert generation["usageDetails"]["output"] > 0
    assert generation["modelParameters"]["temperature"] is not None

    artifact = generation["metadata"]["model_artifact"]
    assert artifact["metadata_complete"] is True
    assert artifact["gguf_sha256"].startswith("sha256:")
    assert artifact["chat_template_sha256"].startswith("sha256:")
    assert artifact["server_binary_sha256"].startswith("sha256:")
    assert artifact["context_size"] > 0
    assert artifact["server_version"]
    assert artifact["gguf_filename"].endswith(".gguf")


def test_forbidden_values_never_reach_the_trace_store(
    langfuse: LangfuseReader, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep credentials, forbidden URLs, and archive paths out of every field.

    The message deliberately carries a token, a database URL, and an archive
    path, so the check is not satisfied merely by nothing having tried.
    """

    archive_root = os.getenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", "").strip()
    fake_token = "987654321:AAFakeTokenValueForTracingTests0123"
    fake_database = "postgresql://intruder:hunter2hunter2@127.0.0.1:5433/finanza"
    parts = [
        "Ho speso 5 euro, token",
        fake_token,
        "database",
        fake_database,
        f"https://api.telegram.org/bot{fake_token}/getMe",
    ]
    if archive_root:
        parts.append(f"{archive_root}/ab/cd/scontrino.jpg")
    trace_id, _payload = _route(capsys, langfuse, text=" ".join(parts))

    exported = json.dumps(langfuse.wait_for(trace_id, _complete_router_trace))

    forbidden = [
        fake_token,
        "hunter2hunter2",
        "api.telegram.org",
        required("LANGFUSE_SECRET_KEY"),
    ]
    for name in ("MONEY_TRACKER_DATABASE_URL", "MONEY_TRACKER_TEST_DATABASE_URL"):
        if value := os.getenv(name, "").strip():
            forbidden.append(value)
    if len(api_key := os.getenv("LLM_API_KEY", "").strip()) >= 12:
        forbidden.append(api_key)
    if archive_root:
        forbidden.append(archive_root)
    leaked = [index for index, value in enumerate(forbidden) if value in exported]
    assert leaked == [], f"forbidden values at positions {leaked} reached the store"
    assert "<redacted-database-url>" in exported
    assert "Ho speso 5 euro" in exported


def test_the_disabled_path_exports_nothing(
    langfuse: LangfuseReader,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit no observation while the switch is off, with a positive control.

    Absence is only evidence once the same query demonstrably finds a traced
    run, so the untraced run is followed by a traced one and both are searched
    by a unique marker in the owner text.
    """

    silent_marker = f"silenzio{uuid4().hex}"
    traced_marker = f"traccia{uuid4().hex}"
    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "false")
    silent_trace, _silent = _route(
        capsys, langfuse, text=f"Ho speso 3 euro per un caffè {silent_marker}"
    )
    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    traced_trace, _traced = _route(
        capsys, langfuse, text=f"Ho speso 4 euro per un cornetto {traced_marker}"
    )

    langfuse.wait_for(traced_trace, _complete_router_trace)
    deadline = time.monotonic() + VISIBILITY_SECONDS
    while not langfuse.matching_input(traced_marker):
        assert time.monotonic() < deadline, "positive control never became searchable"
        time.sleep(1.0)

    assert langfuse.observations(silent_trace) == []
    assert langfuse.matching_input(silent_marker) == []


@dataclass
class RecordingProxy:
    """Loopback proxy recording every request forwarded to the real endpoint.

    Attributes:
        upstream: Root URL of the real inference server.
        requests: `(method, path, body)` for every forwarded request.
    """

    upstream: str
    requests: list[tuple[str, str, bytes]] = field(default_factory=list)


def _proxy_handler(proxy: RecordingProxy) -> type[BaseHTTPRequestHandler]:
    """Build a request handler forwarding verbatim and recording the bytes."""

    class Handler(BaseHTTPRequestHandler):
        """Forward one request and its exact body to the real endpoint."""

        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            proxy.requests.append((self.command, self.path, body))
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() not in {"host", "content-length", "connection"}
            }
            upstream = httpx.request(
                self.command,
                f"{proxy.upstream}{self.path}",
                headers=headers,
                content=body,
                timeout=300.0,
            )
            content = upstream.content
            self.send_response(upstream.status_code)
            self.send_header(
                "Content-Type", upstream.headers.get("content-type", "application/json")
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            """Forward a metadata read such as `/props`."""

            self._forward()

        def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
            """Forward an inference request."""

            self._forward()

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the proxy silent; the recorded requests are the evidence."""

    return Handler


@pytest.fixture
def recording_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingProxy]:
    """Put a recording proxy between the entry point and the real model."""

    base_url = required("LLM_BASE_URL").rstrip("/")
    proxy = RecordingProxy(upstream=base_url.removesuffix("/v1"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _proxy_handler(proxy))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "LLM_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}/v1"
    )
    try:
        yield proxy
    finally:
        server.shutdown()
        server.server_close()


def test_tracing_leaves_the_model_request_byte_identical(
    langfuse: LangfuseReader,
    recording_proxy: RecordingProxy,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send the endpoint exactly the same inference request with tracing on or off.

    The traced run additionally reads `/props`, the server's own report of what
    it loaded, to name the artifact. That is a separate metadata read; the
    inference request itself must not differ by a single byte.
    """

    def completions() -> list[bytes]:
        return [
            body
            for method, path, body in recording_proxy.requests
            if method == "POST" and path == "/v1/chat/completions"
        ]

    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "false")
    _route(capsys, langfuse)
    untraced = completions()
    untraced_paths = [path for _method, path, _body in recording_proxy.requests]
    recording_proxy.requests.clear()

    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    traced_trace, _payload = _route(capsys, langfuse)
    traced = completions()
    traced_paths = [path for _method, path, _body in recording_proxy.requests]

    assert untraced and traced
    assert traced[0] == untraced[0]
    assert set(untraced_paths) == {"/v1/chat/completions"}
    assert {path.split("?")[0] for path in traced_paths} <= {
        "/v1/chat/completions",
        "/props",
    }
    generation = named(
        langfuse.wait_for(traced_trace, _complete_router_trace),
        "model:RouteClassification",
    )
    sent = json.loads(traced[0])
    recorded = json.loads(generation["input"])
    assert recorded["messages"] == sent["messages"]
    assert recorded["response_format"] == sent["response_format"]


@pytest.mark.postgres
def test_a_specialist_dispatch_traces_every_graph_and_its_grounded_result(
    langfuse: LangfuseReader,
    postgres_url: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace routing, the read specialist over the real checkpointer, and facts."""

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", postgres_url)
    main(["profile-init"])
    profile_id = json.loads(capsys.readouterr().out)["profile_id"]
    main(
        [
            "funding-source-add",
            "--profile-id",
            profile_id,
            "--name",
            "Carta",
            "--funding-class",
            FundingClass.PERSONAL_FUNDS.value,
            "--balance-mode",
            BalanceMode.UNTRACKED.value,
            "--default",
        ]
    )
    capsys.readouterr()

    trace_id, payload = _route(
        capsys,
        langfuse,
        "--profile-id",
        profile_id,
        text="Quanto ho speso con la carta questo mese?",
    )

    def complete(observations: list[dict[str, Any]]) -> bool:
        names = {item["name"] for item in observations}
        return {"graph:router", "graph:read", "model:WireToolRequest"} <= names and all(
            item.get("endTime") for item in observations
        )

    if payload["intent"] not in {"query", "advice"}:
        pytest.fail(f"the model routed the question to {payload['intent']}")
    observations = langfuse.wait_for(trace_id, complete)

    dispatch = named(observations, "dispatch")
    assert dispatch["metadata"]["read_selection_policy"] == (
        "explicit-finance-scope-v1"
    )
    assert json.loads(dispatch["output"]) == payload
    selection = named(observations, "model:WireToolRequest")
    assert selection["metadata"]["parse"]["valid"] in {True, False}
    assert {"select_tool"} <= {item["name"] for item in observations}
