"""Real Langfuse, PostgreSQL, and llama.cpp tests for the traced Telegram channel.

Every test drives the production `TelegramGateway`, `TelegramUpdateIngestor`,
`SequentialWorker`, and the worker's own dispatcher composition over the real
repositories, the real checkpointer, the real model, and the running local
Langfuse, then reads the exported observations back through the public API.

The Bot API is the one boundary not reached for real, as in every other
channel test: the production `BotApiClient` builds its token-bearing requests
and parses the answers, but an in-process HTTP transport answers them. That
keeps the owner's test bot silent and still proves that the token in the
request URL never reaches a trace.

Traces use the `integration-test` environment and each test deletes the traces
it created.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver

from money_tracker.application import CategoryProposalService, CategoryReviewService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryProposal,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.messaging import (
    BotEnvironment,
    OutboxMessageDraft,
    telegram_source_reference,
)
from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.presentation import TelegramResponseRenderer
from money_tracker.runtime import LocalStructuredOutputModel, build_review_graph
from money_tracker.telegram import (
    BotApiClient,
    TelegramGateway,
    TelegramSettings,
    TelegramUpdateIngestor,
)
from money_tracker.tracing import DISABLED_TRACER, Tracer, open_tracer
from money_tracker.tracing.langfuse import trace_id_for
from money_tracker.worker import SequentialWorker
from money_tracker.worker.cli import _CurrentTaxonomyDispatcher
from money_tracker.workflows import (
    CATEGORY_FIT,
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    DispatchStatus,
    RoutingDecision,
    RoutingReason,
    RoutingSource,
    WorkflowIntent,
    default_prompt,
    initial_review_state,
)
from money_tracker.workflows.candidates import DEFAULT_CATEGORY_FIT_CANDIDATE
from money_tracker.workflows.resolution import PRODUCT_TIMEZONE
from tests.integration.money_tracker.tracing.store import (
    VISIBILITY_SECONDS,
    LangfuseReader,
    complete,
    named,
    required,
)

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

BOT_TOKEN = "246813579:AAChannelTracingIntegrationTokenValue01"
OWNER_ID = 770019283
EXPENSE_TEXT = "Ho speso 12 euro per i croccantini dei gatti"
STAGES = {
    "telegram-ingest",
    "work-unit",
    "dispatch",
    "graph:router",
    "render",
    "commit",
    "telegram-delivery",
}


def _ok(result: object) -> httpx.Response:
    """Answer like a successful Bot API call."""

    return httpx.Response(200, json={"ok": True, "result": result})


@dataclass
class BotApi:
    """In-process Bot API answering the production client.

    Attributes:
        pending: Raw updates the next `getUpdates` returns.
        sent: Every `sendMessage` body with the identity it was given.
        methods: Every method the client called, in order.
    """

    pending: list[dict[str, Any]] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    _next_update_id: int = 1
    _next_message_id: int = 500

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one Bot API request."""

        method = request.url.path.rsplit("/", 1)[-1]
        self.methods.append(method)
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            batch = [
                item for item in self.pending if item["update_id"] >= body["offset"]
            ]
            self.pending = []
            return _ok(batch)
        if method == "sendMessage":
            self._next_message_id += 1
            self.sent.append({"message_id": self._next_message_id, **body})
            return _ok({"message_id": self._next_message_id})
        if method in {"answerCallbackQuery", "editMessageReplyMarkup"}:
            return _ok(True)
        return httpx.Response(404, json={"ok": False, "error_code": 404})

    def text(self, text: str, *, reply_to: int | None = None) -> int:
        """Queue one private owner message and return its update identity."""

        update_id = self._take_update_id()
        message: dict[str, Any] = {
            "message_id": 10_000 + update_id,
            "date": int(time.time()),
            "chat": {"id": OWNER_ID, "type": "private"},
            "from": {"id": OWNER_ID, "is_bot": False},
            "text": text,
        }
        if reply_to is not None:
            message["reply_to_message"] = {"message_id": reply_to}
        self.pending.append({"update_id": update_id, "message": message})
        return update_id

    def press(self, label: str) -> int:
        """Queue a press of the newest delivered control carrying one label."""

        for sent in reversed(self.sent):
            for row in sent.get("reply_markup", {}).get("inline_keyboard", []):
                for button in row:
                    if button["text"] == label:
                        update_id = self._take_update_id()
                        self.pending.append(
                            {
                                "update_id": update_id,
                                "callback_query": {
                                    "id": f"callback-{update_id}",
                                    "from": {"id": OWNER_ID, "is_bot": False},
                                    "data": button["callback_data"],
                                    "message": {
                                        "message_id": sent["message_id"],
                                        "date": int(time.time()),
                                        "chat": {"id": OWNER_ID, "type": "private"},
                                    },
                                },
                            }
                        )
                        return update_id
        raise AssertionError(f"no delivered control is labelled {label!r}")

    def last_message_id(self) -> int:
        """Return the identity of the newest delivered message."""

        return int(self.sent[-1]["message_id"])

    def _take_update_id(self) -> int:
        update_id = self._next_update_id
        self._next_update_id += 1
        return update_id


@dataclass
class Channel:
    """The production gateway and worker over one isolated paired bot.

    Attributes:
        bot_api: Bot API answering the gateway's transport.
        gateway: Production gateway with the injected tracer.
        worker: Production worker and dispatcher composition.
        finance: Real finance repository.
        profile: The paired owner's profile.
        card: The profile's default funding source.
        bot_key: Unique persistence scope, which keeps trace identities unique.
        tracer: Tracer injected into every component.
        checkpointer: Real PostgreSQL checkpointer the worker resumes graphs from.
        messaging: Real messaging repository.
        settings: Explicit test-bot settings.
    """

    bot_api: BotApi
    gateway: TelegramGateway
    worker: SequentialWorker
    finance: PostgresFinanceRepository
    profile: Profile
    card: FundingSource
    bot_key: str
    tracer: Tracer
    checkpointer: BaseCheckpointSaver[str]
    messaging: PostgresMessagingRepository
    settings: TelegramSettings

    def exchange(self, update_id: int) -> str:
        """Carry one queued update through ingest, work, and delivery.

        Returns:
            The update's source reference, from which its trace is derived.
        """

        self.gateway.run_once()
        while self.worker.process_once():
            pass
        while self.gateway.deliver_outbox_once():
            pass
        return telegram_source_reference(self.bot_key, update_id)

    def traced_with(self, tracer: Tracer) -> Channel:
        """Rebuild the same channel's components with another tracer."""

        gateway, worker = _components(
            bot_api=self.bot_api,
            settings=self.settings,
            messaging=self.messaging,
            finance=self.finance,
            checkpointer=self.checkpointer,
            tracer=tracer,
        )
        return replace(self, gateway=gateway, worker=worker, tracer=tracer)


def _components(
    *,
    bot_api: BotApi,
    settings: TelegramSettings,
    messaging: PostgresMessagingRepository,
    finance: PostgresFinanceRepository,
    checkpointer: BaseCheckpointSaver[str],
    tracer: Tracer,
) -> tuple[TelegramGateway, SequentialWorker]:
    """Compose the gateway and worker exactly as their entry points do."""

    transport = BotApiClient(
        BOT_TOKEN,
        settings,
        client=httpx.Client(transport=httpx.MockTransport(bot_api.handle)),
    )
    gateway = TelegramGateway(
        settings=settings,
        transport=transport,
        repository=messaging,
        ingestor=TelegramUpdateIngestor(messaging),
        clock=SystemClock(),
        tracer=tracer,
    )
    ids = UUID4Generator()
    worker = SequentialWorker(
        bot_key=settings.bot_key,
        repository=messaging,
        configuration=finance,
        review=CategoryReviewService(finance, finance, clock=SystemClock(), ids=ids),
        dispatcher=_CurrentTaxonomyDispatcher(
            repository=finance,
            model=LocalStructuredOutputModel(tracer=tracer),
            ids=ids,
            checkpointer=checkpointer,
            tracer=tracer,
        ),
        renderer=TelegramResponseRenderer(),
        clock=SystemClock(),
        tracer=tracer,
    )
    return gateway, worker


@contextmanager
def _channel(postgres_url: str, tracer: Tracer) -> Iterator[Channel]:
    """Migrate, seed one profile, pair the owner, and compose the channel."""

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    now = datetime.now(UTC)
    finance = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    finance.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    finance.create_funding_source(card, occurred_at=now)
    messaging = PostgresMessagingRepository(postgres_url)
    bot_key = f"test-{uuid4().hex}"
    messaging.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=now)
    code_hash = sha256(f"code-{bot_key}".encode()).hexdigest()
    messaging.create_pairing_code(
        code_id=uuid4(),
        bot_key=bot_key,
        profile_id=profile.id,
        code_hash=code_hash,
        expires_at=now + timedelta(minutes=10),
        now=now,
    )
    confirmation = "Fatto: sei il proprietario del bot."
    assert messaging.pair_owner(
        bot_key=bot_key,
        code_hash=code_hash,
        telegram_user_id=OWNER_ID,
        telegram_chat_id=OWNER_ID,
        confirmation=OutboxMessageDraft(
            id=uuid4(),
            bot_key=bot_key,
            telegram_chat_id=OWNER_ID,
            logical_key="pairing:owner",
            response_kind="paired",
            text=confirmation,
            content_hash=sha256(confirmation.encode()).hexdigest(),
        ),
        now=now,
    )
    settings = TelegramSettings(
        environment=BotEnvironment.TEST,
        bot_key=bot_key,
        token_file=Path("/nonexistent/test-token"),
    )
    bot_api = BotApi()
    with open_postgres_checkpointer(postgres_url) as checkpointer:
        gateway, worker = _components(
            bot_api=bot_api,
            settings=settings,
            messaging=messaging,
            finance=finance,
            checkpointer=checkpointer,
            tracer=tracer,
        )
        # The pairing confirmation answers no update, so it is delivered
        # untraced and every test starts from an empty outbox.
        assert gateway.deliver_outbox_once()
        yield Channel(
            bot_api=bot_api,
            gateway=gateway,
            worker=worker,
            finance=finance,
            profile=profile,
            card=card,
            bot_key=bot_key,
            tracer=tracer,
            checkpointer=checkpointer,
            messaging=messaging,
            settings=settings,
        )


def _trace(langfuse: LangfuseReader, reference: str) -> str:
    """Register a source reference's trace for deletion and return its id."""

    trace_id = trace_id_for(reference)
    langfuse.created.append(trace_id)
    return trace_id


def _generations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the model generations of one trace."""

    return [item for item in observations if item["name"].startswith("model:")]


def _assert_complete_calls(observations: list[dict[str, Any]], minimum: int) -> None:
    """Require complete input, output, usage, and artifact identity per call."""

    generations = _generations(observations)
    assert len(generations) >= minimum, sorted(item["name"] for item in observations)
    by_id = {item["id"]: item for item in observations}
    for generation in generations:
        request = json.loads(generation["input"])
        assert request["messages"][0]["role"] == "system"
        assert request["messages"][0]["content"]
        assert request["response_format"]["json_schema"]["strict"] is True
        assert generation["output"]
        assert generation["usageDetails"]["input"] > 0
        assert "parse" in generation["metadata"]
        artifact = generation["metadata"]["model_artifact"]
        assert artifact["metadata_complete"] is True, artifact
        assert artifact["gguf_sha256"].startswith("sha256:")
        parent = by_id[generation["parentObservationId"]]
        assert not parent["name"].startswith(("dispatch", "work-unit"))


def test_an_owner_message_produces_one_end_to_end_trace(
    langfuse: LangfuseReader, postgres_url: str
) -> None:
    """Join ingest, claim, dispatch, model calls, commit, and delivery."""

    tracer = open_tracer()
    with _channel(postgres_url, tracer) as channel:
        reference = channel.exchange(channel.bot_api.text(EXPENSE_TEXT))
        delivered = channel.bot_api.sent[-1]
    tracer.shutdown()
    trace_id = _trace(langfuse, reference)

    observations = langfuse.wait_for(trace_id, complete(STAGES))
    dispatch = named(observations, "dispatch")
    intent = dispatch["metadata"]["routing_intent"]
    if intent != "expense":
        pytest.fail(f"the model routed the expense to {intent}")
    observations = langfuse.wait_for(
        trace_id, complete(STAGES | {"graph:expense", "model:RouteClassification"})
    )

    ingest = named(observations, "telegram-ingest")
    unit = named(observations, "work-unit")
    dispatch = named(observations, "dispatch")
    render = named(observations, "render")
    commit = named(observations, "commit")
    delivery = named(observations, "telegram-delivery")
    # Each seeded stage is a root of the shared trace. The SDK hangs it under a
    # synthetic remote parent, so the store's own root flag is what says so.
    by_id = {item["id"]: item for item in observations}
    for root in (ingest, unit, delivery):
        assert root["isRootObservation"] is True, root["name"]
        assert root["parentObservationId"] not in by_id, root["name"]
    for child in (dispatch, render, commit):
        assert child["parentObservationId"] == unit["id"], child["name"]
    assert ingest["startTime"] <= unit["startTime"] <= delivery["startTime"]

    assert json.loads(ingest["input"])["text"] == EXPENSE_TEXT
    assert unit["metadata"]["inbox_id"] == ingest["metadata"]["inbox_id"]
    assert unit["metadata"]["work_unit_outcome"] == "committed"
    assert json.loads(dispatch["input"])["message"]["source_reference"] == reference
    assert dispatch["metadata"]["specialist"] == "expense"
    assert dispatch["metadata"]["thread_ids"][:2] == [
        f"router:{reference}",
        f"expense:{reference}",
    ]
    assert dispatch["metadata"]["router_prompt"]
    assert dispatch["metadata"]["read_selection_policy"] == "explicit-finance-scope-v1"
    rendered = json.loads(render["output"])
    committed = json.loads(commit["output"])
    sent = json.loads(delivery["input"])
    assert sent["outbox_id"] == committed["outbox_id"]
    assert sent["text"] == rendered["text"] == delivered["text"]
    assert (
        json.loads(delivery["output"])["telegram_message_id"]
        == (delivered["message_id"])
    )
    _assert_complete_calls(observations, minimum=2)


def test_every_worker_specialist_traces_its_model_calls(
    langfuse: LangfuseReader, postgres_url: str
) -> None:
    """Trace expense, query, advice, correction, and undo through the worker."""

    tracer = open_tracer()
    with _channel(postgres_url, tracer) as channel:
        api = channel.bot_api
        expense = channel.exchange(api.text(EXPENSE_TEXT))
        query = channel.exchange(api.text("Quanto ho speso con la carta questo mese?"))
        advice = channel.exchange(api.text("Quali categorie sono a rischio budget?"))
        modify = channel.exchange(api.press("Modifica"))
        correction = channel.exchange(
            api.text("L'importo giusto è 15 euro", reply_to=api.last_message_id())
        )
        undo = channel.exchange(api.press("Annulla"))
    tracer.shutdown()

    expected = {
        expense: ("expense", 2),
        query: ("read", 2),
        advice: ("read", 2),
        modify: ("movement-change", 0),
        correction: ("movement-change", 2),
        undo: ("movement-change", 0),
    }
    for reference, (specialist, minimum_calls) in expected.items():
        trace_id = _trace(langfuse, reference)
        observations = langfuse.wait_for(trace_id, complete(STAGES))
        metadata = named(observations, "dispatch")["metadata"]
        assert metadata.get("specialist") == specialist, (reference, metadata)
        if minimum_calls:
            _assert_complete_calls(observations, minimum=minimum_calls)
    query_metadata = named(langfuse.observations(trace_id_for(query)), "dispatch")[
        "metadata"
    ]
    assert query_metadata["read_admitted_tools"]
    assert query_metadata["read_grounded"] is True
    correction_metadata = named(
        langfuse.observations(trace_id_for(correction)), "dispatch"
    )["metadata"]
    assert correction_metadata["routing_intent"] == "correction"
    assert "extraction" in correction_metadata


def _pending_review(channel: Channel) -> DispatchOutcome:
    """Store a draft and a pending proposal, then open its paused review.

    Returns:
        The outcome the expense specialist returns when it opens this review.
    """

    clock = SystemClock()
    reference = f"integration:review:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=channel.profile.id,
        amount=Money(4000),
        occurred_at=clock.now().astimezone(PRODUCT_TIMEZONE),
        description="lezione di ceramica",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=channel.card.id, amount=Money(4000)),
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    stored = channel.finance.create_expense(
        expense,
        audit_event=AuditEvent(
            id=uuid4(),
            profile_id=channel.profile.id,
            aggregate_type="expense",
            aggregate_id=expense.id,
            action="create",
            actor="test",
            source_reference=reference,
            occurred_at=clock.now(),
            prior_version=None,
            current_version=1,
            reason="integration fixture",
        ),
        request_hash=sha256(reference.encode()).hexdigest(),
    )
    proposal = CategoryProposalService(
        channel.finance, clock=clock, ids=UUID4Generator()
    ).submit(
        CategoryProposal(
            id=uuid4(),
            profile_id=channel.profile.id,
            proposed_name="Corsi creativi",
            description="Lezioni e corsi di attività manuali.",
            parent_id=None,
            rationale="Nessuna categoria attiva descrive lo scopo.",
            alternative_category_ids=(),
            excluded_names=(),
            source_reference=reference,
        ),
        actor="workflow",
        reason="no active category is adequate",
    )
    build_review_graph(
        model=LocalStructuredOutputModel(),
        configuration=channel.finance,
        review=CategoryReviewService(
            channel.finance, channel.finance, clock=clock, ids=UUID4Generator()
        ),
        category_instructions=default_prompt(CATEGORY_FIT).text,
        category_candidate=DEFAULT_CATEGORY_FIT_CANDIDATE,
        checkpointer=channel.checkpointer,
    ).invoke(
        initial_review_state(
            profile_id=channel.profile.id,
            proposal_id=proposal.id,
            expense_id=stored.id,
            source_reference=reference,
        ),
        {"configurable": {"thread_id": f"category-review:{proposal.id}"}},
    )
    return DispatchOutcome(
        routing=RoutingDecision(
            intent=WorkflowIntent.EXPENSE,
            source=RoutingSource.MODEL,
            reason=RoutingReason.MODEL_CLASSIFICATION,
            confidence=0.99,
        ),
        status=DispatchStatus.AWAITING_CATEGORY_APPROVAL,
        expense_id=stored.id,
        proposal_id=proposal.id,
    )


@dataclass
class _OpenedReview:
    """Setup-only dispatch port answering as the expense specialist did.

    Whether a real message makes the model propose a category is not
    deterministic, so the turn that asks the review question is replayed from
    the outcome that turn returns. The resume under test runs through the
    production dispatcher and the real model.
    """

    outcome: DispatchOutcome

    def dispatch(
        self, message: ChannelMessage, *, context: DispatchContext
    ) -> DispatchOutcome:
        """Return the outcome that opened the paused review."""

        return self.outcome


def test_a_resumed_category_review_traces_its_reproposal(
    langfuse: LangfuseReader, postgres_url: str
) -> None:
    """Resume the paused review from its own control and trace the new call."""

    tracer = open_tracer()
    with _channel(postgres_url, tracer) as channel:
        opened = _pending_review(channel)
        untraced = channel.traced_with(DISABLED_TRACER)
        asking = SequentialWorker(
            bot_key=channel.bot_key,
            repository=channel.messaging,
            configuration=channel.finance,
            review=CategoryReviewService(
                channel.finance,
                channel.finance,
                clock=SystemClock(),
                ids=UUID4Generator(),
            ),
            dispatcher=_OpenedReview(opened),
            renderer=TelegramResponseRenderer(),
            clock=SystemClock(),
        )
        channel.bot_api.text("Ho speso 40 euro per una lezione di ceramica")
        untraced.gateway.run_once()
        assert asking.process_once()
        while untraced.gateway.deliver_outbox_once():
            pass
        resumed = channel.exchange(channel.bot_api.press("Proponine un'altra"))
    tracer.shutdown()

    trace_id = _trace(langfuse, resumed)
    observations = langfuse.wait_for(trace_id, complete(STAGES))
    metadata = named(observations, "dispatch")["metadata"]
    assert metadata["specialist"] == "category-review"
    assert metadata["review_decision_action"] == "repropose_category"
    assert metadata["review_resumed"] is True
    assert metadata["review_proposal_id"] == str(opened.proposal_id)
    assert metadata["thread_ids"][-1] == f"category-review:{opened.proposal_id}"
    assert metadata["routing_source"] == "deterministic"
    _assert_complete_calls(observations, minimum=1)


def test_forbidden_values_never_reach_a_channel_trace(
    langfuse: LangfuseReader, postgres_url: str
) -> None:
    """Keep credentials, URLs, archive paths, and Telegram identities out.

    The owner message deliberately carries a token, a database URL, a Bot API
    URL, and an archive path, and the transport's own requests carry the bot
    token in their URL, so absence is not merely the absence of an attempt.
    """

    archive_root = os.getenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", "").strip()
    pasted_token = "987654321:AAFakeTokenValueForChannelTests0123"
    pasted_database = "postgresql://intruder:hunter2hunter2@127.0.0.1:5433/finanza"
    parts = [
        "Ho speso 5 euro, token",
        pasted_token,
        "database",
        pasted_database,
        f"https://api.telegram.org/bot{pasted_token}/getMe",
    ]
    if archive_root:
        parts.append(f"{archive_root}/ab/cd/scontrino.jpg")
    tracer = open_tracer()
    with _channel(postgres_url, tracer) as channel:
        reference = channel.exchange(channel.bot_api.text(" ".join(parts)))
    tracer.shutdown()

    exported = json.dumps(
        langfuse.wait_for(_trace(langfuse, reference), complete(STAGES))
    )

    forbidden = [
        BOT_TOKEN,
        pasted_token,
        "hunter2hunter2",
        "api.telegram.org",
        str(OWNER_ID),
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


def test_the_disabled_channel_exports_nothing(
    langfuse: LangfuseReader,
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit nothing from the gateway or worker while the switch is off.

    The silent message is followed by a traced one through the same channel,
    and absence counts only once the traced marker is searchable.
    """

    silent_marker = f"silenzio{uuid4().hex}"
    traced_marker = f"traccia{uuid4().hex}"
    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "false")
    disabled = open_tracer()
    monkeypatch.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    traced = open_tracer()
    with _channel(postgres_url, disabled) as channel:
        silent = channel.exchange(
            channel.bot_api.text(f"Ho speso 3 euro per un caffè {silent_marker}")
        )
        loud = channel.traced_with(traced)
        heard = loud.exchange(
            channel.bot_api.text(f"Ho speso 4 euro per un cornetto {traced_marker}")
        )
    traced.shutdown()
    _trace(langfuse, silent)

    langfuse.wait_for(_trace(langfuse, heard), complete(STAGES))
    deadline = time.monotonic() + VISIBILITY_SECONDS
    while not langfuse.matching_input(traced_marker):
        assert time.monotonic() < deadline, "positive control never became searchable"
        time.sleep(1.0)

    assert disabled.enabled is False
    assert langfuse.observations(trace_id_for(silent)) == []
    assert langfuse.matching_input(silent_marker) == []
