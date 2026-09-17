"""Real HTTP, PostgreSQL, llama.cpp, and Langfuse tests for traced browser advice.

Every test starts the production `money-tracker-api run` entry point as its own
process on a free loopback port against the isolated test database, sends real
HTTP requests through its Host, Origin, and CSRF checks, and reads the exported
observations back through Langfuse's public API. The request's trace is found
through the content-free `web.advice.completed` journal event, which names the
request reference the trace is derived from.

Traces use the `integration-test` environment and each test deletes the traces
it created.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from money_tracker.application import FinanceAdministrationService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    FundingClass,
    FundingSource,
    Money,
    MonthlyBudget,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.tracing.langfuse import trace_id_for
from tests.integration.money_tracker.tracing.store import (
    ENVIRONMENT,
    VISIBILITY_SECONDS,
    LangfuseReader,
    complete,
    named,
    required,
)
from tests.integration.money_tracker.tracing.test_langfuse_tracing import (
    RecordingProxy,
    _proxy_handler,
)
from tests.integration.money_tracker.web.test_api import _store_posted_expense

pytestmark = [
    pytest.mark.langfuse,
    pytest.mark.inference,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("RUN_LANGFUSE_INTEGRATION_TESTS") != "1",
        reason=(
            "set RUN_LANGFUSE_INTEGRATION_TESTS=1 with the observability profile "
            "and the local model running"
        ),
    ),
]

ROME = ZoneInfo("Europe/Rome")
ROOT = Path(__file__).resolve().parents[4]
BUDGET_QUESTION = "Come vanno tutti i miei budget questo mese?"
READY_SECONDS = 60.0
STOP_SECONDS = 30.0
STAGES = {"advice-request", "graph:read", "select_tool", "model:WireToolRequest"}


def _seed(postgres_url: str) -> None:
    """Migrate and seed the single profile the API requires, with a budget."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    taxonomy = initial_taxonomy()
    now = datetime.now(ROME)
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta advice tracciata",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    _store_posted_expense(repository, profile=profile, source=card)
    FinanceAdministrationService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    ).create_budget(
        MonthlyBudget(
            id=uuid4(),
            profile_id=profile.id,
            category_id=taxonomy.resolve_active("Spesa alimentare").id,
            recurring_target=Money(4000),
        ),
        idempotency_key=f"integration-traced-advice-budget:{uuid4()}",
        actor="test",
        reason="traced advice integration fixture",
    )


def _free_port() -> int:
    """Return a loopback port nothing listens on at the moment."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class ApiProcess:
    """The production API process and the journal it writes.

    Attributes:
        origin: Exact allowed browser origin of the listener.
        process: Running `money-tracker-api run` process.
        journal: File receiving the process's structured journal.
        stop_seconds: How long the process took to exit after SIGINT.
        exit_code: The process's exit code once stopped.
    """

    origin: str
    process: subprocess.Popen[bytes]
    journal: Path
    stop_seconds: float | None = None
    exit_code: int | None = None

    def ask(self, message: str) -> httpx.Response:
        """Send one advice request through the browser security contract."""

        with httpx.Client(base_url=self.origin, timeout=120.0) as client:
            token = client.get("/api/v1/csrf", headers={"origin": self.origin}).json()[
                "token"
            ]
            return client.post(
                "/api/v1/advice",
                headers={"origin": self.origin, "x-csrf-token": token},
                json={"message": message},
            )

    def events(self, name: str) -> list[dict[str, Any]]:
        """Return the journal records carrying one event name."""

        records = []
        for line in self.journal.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("event") == name:
                records.append(record)
        return records

    def last_reference(self) -> str:
        """Return the request reference of the newest completed advice."""

        completed = self.events("web.advice.completed")
        assert completed, "the API recorded no completed advice"
        return str(completed[-1]["request_reference"])


@contextmanager
def _api(
    postgres_url: str,
    tmp_path: Path,
    *,
    tracing: bool,
    llm_base_url: str | None = None,
    overrides: dict[str, str] | None = None,
) -> Iterator[ApiProcess]:
    """Run the production API entry point until the block ends, then stop it."""

    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment.update(
        {
            "MONEY_TRACKER_DATABASE_URL": postgres_url,
            "MONEY_TRACKER_WEB_HOST": "127.0.0.1",
            "MONEY_TRACKER_WEB_PORT": str(port),
            "MONEY_TRACKER_WEB_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "MONEY_TRACKER_WEB_ALLOWED_ORIGINS": f"{origin},http://localhost:{port}",
            "MONEY_TRACKER_WEB_STATIC_DIR": str(ROOT / "frontend/dist"),
            "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": "test",
            "MONEY_TRACKER_TRACING_ENABLED": "true" if tracing else "false",
            "LANGFUSE_TRACING_ENVIRONMENT": ENVIRONMENT,
            "LOG_LEVEL": "INFO",
            "LOG_FORMAT": "json",
        }
    )
    if llm_base_url is not None:
        environment["LLM_BASE_URL"] = llm_base_url
    environment.update(overrides or {})
    journal = tmp_path / f"api-{port}.jsonl"
    with journal.open("wb") as sink:
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/money-tracker-api"), "run"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=sink,
        )
    api = ApiProcess(origin=origin, process=process, journal=journal)
    try:
        deadline = time.monotonic() + READY_SECONDS
        while True:
            assert process.poll() is None, f"the API exited with {process.returncode}"
            try:
                if httpx.get(f"{origin}/api/v1/health/ready", timeout=2.0).is_success:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "the API never became ready"
            time.sleep(0.25)
        yield api
    finally:
        started = time.monotonic()
        process.send_signal(signal.SIGINT)
        try:
            api.exit_code = process.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            api.exit_code = process.wait()
        api.stop_seconds = time.monotonic() - started


def test_a_browser_advice_request_is_one_complete_trace(
    langfuse: LangfuseReader, postgres_url: str, tmp_path: Path
) -> None:
    """Trace the request, the read graph, and the selection call completely."""

    _seed(postgres_url)
    with _api(postgres_url, tmp_path, tracing=True) as api:
        response = api.ask(BUDGET_QUESTION)
        reference = api.last_reference()
    trace_id = trace_id_for(reference)
    langfuse.created.append(trace_id)

    assert response.status_code == 200
    payload = response.json()
    assert api.exit_code == 0
    assert api.stop_seconds is not None and api.stop_seconds < 10.0
    assert api.events("tracing.enabled")
    assert reference.startswith("web:advice:") and reference != "web:advice"

    observations = langfuse.wait_for(trace_id, complete(STAGES))
    root = named(observations, "advice-request")
    generation = named(observations, "model:WireToolRequest")
    assert root["isRootObservation"] is True
    assert json.loads(root["input"])["message"] == BUDGET_QUESTION
    assert root["metadata"]["routing_intent"] == "advice"
    assert root["metadata"]["read_selection_policy"] == "explicit-finance-scope-v1"
    assert root["metadata"]["read_admitted_tools"]
    if payload["status"] == "answered":
        assert root["metadata"]["read_grounded"] is True
        assert (
            json.loads(root["output"])["executed_tool"] == payload["evidence"]["kind"]
        )
    request = json.loads(generation["input"])
    assert request["messages"][-1] == {"role": "user", "content": BUDGET_QUESTION}
    assert request["response_format"]["json_schema"]["strict"] is True
    assert generation["output"]
    assert generation["usageDetails"]["input"] > 0
    assert generation["metadata"]["model_artifact"]["metadata_complete"] is True


def test_forbidden_values_never_reach_an_advice_trace(
    langfuse: LangfuseReader, postgres_url: str, tmp_path: Path
) -> None:
    """Keep credentials, forbidden URLs, and archive paths out of the trace."""

    archive_root = os.getenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", "").strip()
    pasted_token = "987654321:AAFakeTokenValueForAdviceTests01234"
    pasted_database = "postgresql://intruder:hunter2hunter2@127.0.0.1:5433/finanza"
    parts = [
        "Come vanno i budget? token",
        pasted_token,
        "database",
        pasted_database,
        f"https://api.telegram.org/bot{pasted_token}/getMe",
    ]
    if archive_root:
        parts.append(f"{archive_root}/ab/cd/scontrino.jpg")
    _seed(postgres_url)
    with _api(postgres_url, tmp_path, tracing=True) as api:
        api.ask(" ".join(parts))
        reference = api.last_reference()
    trace_id = trace_id_for(reference)
    langfuse.created.append(trace_id)

    exported = json.dumps(langfuse.wait_for(trace_id, complete(STAGES)))

    forbidden = [
        pasted_token,
        "hunter2hunter2",
        "api.telegram.org",
        required("LANGFUSE_SECRET_KEY"),
        postgres_url,
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
    assert "Come vanno i budget?" in exported


def test_the_disabled_api_exports_nothing(
    langfuse: LangfuseReader, postgres_url: str, tmp_path: Path
) -> None:
    """Emit nothing from an untraced API, against a traced positive control."""

    silent_marker = f"silenzio{uuid4().hex}"
    traced_marker = f"traccia{uuid4().hex}"
    _seed(postgres_url)
    with _api(postgres_url, tmp_path, tracing=False) as silent_api:
        silent_api.ask(f"{BUDGET_QUESTION} {silent_marker}")
        silent_reference = silent_api.last_reference()
    with _api(postgres_url, tmp_path, tracing=True) as loud_api:
        loud_api.ask(f"{BUDGET_QUESTION} {traced_marker}")
        loud_reference = loud_api.last_reference()
    langfuse.created.extend(
        [trace_id_for(silent_reference), trace_id_for(loud_reference)]
    )

    langfuse.wait_for(trace_id_for(loud_reference), complete(STAGES))
    deadline = time.monotonic() + VISIBILITY_SECONDS
    while not langfuse.matching_input(traced_marker):
        assert time.monotonic() < deadline, "positive control never became searchable"
        time.sleep(1.0)

    assert silent_api.events("tracing.enabled") == []
    assert silent_api.exit_code == 0
    assert langfuse.observations(trace_id_for(silent_reference)) == []
    assert langfuse.matching_input(silent_marker) == []


@pytest.fixture
def advice_proxy() -> Iterator[tuple[RecordingProxy, str]]:
    """Put a recording proxy between the API process and the real model.

    Yields:
        The proxy's recorded requests and the base URL the API must use.
    """

    base_url = required("LLM_BASE_URL").rstrip("/")
    proxy = RecordingProxy(upstream=base_url.removesuffix("/v1"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _proxy_handler(proxy))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield proxy, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def test_tracing_leaves_the_advice_model_request_byte_identical(
    langfuse: LangfuseReader,
    postgres_url: str,
    tmp_path: Path,
    advice_proxy: tuple[RecordingProxy, str],
) -> None:
    """Send the endpoint the same selection request with tracing off and on."""

    proxy, address = advice_proxy

    def completions() -> list[bytes]:
        return [
            body
            for method, path, body in proxy.requests
            if method == "POST" and path == "/v1/chat/completions"
        ]

    _seed(postgres_url)
    with _api(postgres_url, tmp_path, tracing=False, llm_base_url=address) as plain:
        plain.ask(BUDGET_QUESTION)
    untraced = completions()
    proxy.requests.clear()
    with _api(postgres_url, tmp_path, tracing=True, llm_base_url=address) as traced:
        traced.ask(BUDGET_QUESTION)
        reference = traced.last_reference()
    langfuse.created.append(trace_id_for(reference))
    traced_requests = completions()

    assert untraced and traced_requests
    assert traced_requests[0] == untraced[0]
    generation = named(
        langfuse.wait_for(trace_id_for(reference), complete(STAGES)),
        "model:WireToolRequest",
    )
    sent = json.loads(traced_requests[0])
    recorded = json.loads(generation["input"])
    assert recorded["messages"] == sent["messages"]
    assert recorded["response_format"] == sent["response_format"]
