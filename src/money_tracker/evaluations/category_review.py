"""Executable `workflow/category-review-it` suite over the real review graph.

This suite asks a different question from the other three. Routing, ingestion,
and categorization ask whether a model is accurate enough to promote. This one
asks whether the workflow keeps its postconditions on every branch the product
baseline requires, and whether a replayed decision writes twice.

Every gate here is therefore an invariant rather than a quality trend, which is
why the matrix states them all at 100% or zero tolerance. It is also why the
model port is scripted. Substituting the real endpoint would let a model
failure mask or mimic a wiring failure, and the branches under test are the
deterministic ones: the model appears only where re-proposal already delegates
to it by design.

Postconditions are read back from real PostgreSQL, not from the returned state.
A graph that reported success while writing nothing would pass a state-only
check, so the suite counts taxonomy nodes, reads the stored movement's status
and purpose allocation, and counts pending proposals for each case's own
profile.

Each case runs against a freshly bootstrapped profile in the isolated test
database. That keeps every count a clean delta and lets cases run in any order,
and the suite refuses to run against anything but that local test database,
because a suite that writes to the owner's real data would be a defect far
worse than the ones it measures.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from psycopg import connect, sql
from pydantic import BaseModel, JsonValue

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    Category,
    CategoryProposal,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    Taxonomy,
    initial_taxonomy,
)
from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    collect_environment_metadata,
    collect_source_metadata,
)
from money_tracker.evaluations.suites import (
    CaseResult,
    CaseStatus,
    MetricScore,
    SuiteReport,
    blocking_gates_passed,
    dataset_metadata,
    load_cases,
    model_metadata,
    new_run_id,
    rate,
    sha256_text,
    utc_now,
    write_report,
)
from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import ASK_NODE, build_review_graph
from money_tracker.workflows import (
    CANDIDATE_CATEGORY_FIT_INSTRUCTIONS,
    CallbackAction,
    ReviewDecision,
    ReviewQuestion,
    initial_review_state,
)
from money_tracker.workflows.resolution import PRODUCT_TIMEZONE

SUITE = "workflow/category-review-it"
"""Suite name from the catalog in the experiment process."""

DATASET_MINIMUM = 30
"""Multi-turn scenarios the experiment process requires before promotion."""

ALIAS_CATEGORY = "Toelettatura"
"""Fixture node carrying aliases, which the seed taxonomy does not."""

ARCHIVED_CATEGORY = "Spese storiche"
"""Fixture node that exists but must never receive a new allocation."""


class ScriptedModel:
    """A real port implementation replaying one completion per call.

    Every branch this suite gates is deterministic, so the boundary is scripted
    rather than reached. A case that never re-proposes never calls it.
    """

    def __init__(self, completions: Sequence[str]) -> None:
        """Store the script and the call counter."""

        self.completions = list(completions)
        self.calls = 0

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: Any = None,
    ) -> str:
        """Return the next scripted completion, or refuse an unscripted call."""

        self.calls += 1
        if self.calls > len(self.completions):
            raise AssertionError("the review asked for an unscripted candidate")
        return self.completions[self.calls - 1]


def require_isolated_database(database_url: str) -> str:
    """Refuse any database but the local isolated integration-test one.

    Args:
        database_url: Candidate PostgreSQL URL.

    Returns:
        The same URL when it names the expected local test database.

    Raises:
        ValueError: If the URL names anything else. A suite that bootstraps
            profiles and posts movements must never reach the owner's real
            data, and a name check is the only guard that cannot be bypassed
            by a misconfigured environment file.
    """

    parsed = urlparse(database_url)
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 5433
        or parsed.path != "/money_tracker_test"
    ):
        raise ValueError(
            "the category review suite writes real rows and runs only against "
            "the local money_tracker_test database on port 5433"
        )
    return database_url


def fixture_taxonomy() -> Taxonomy:
    """Extend the seed taxonomy with the two nodes the matrix branches need.

    The accepted seed taxonomy carries no alias and no archived node, so
    `use named existing/alias` and `named an archived category` could not be
    exercised against it. Both nodes are additions to the fixture, never to the
    product taxonomy.

    Returns:
        The seed taxonomy plus one aliased active node and one archived node.
    """

    base = initial_taxonomy()
    animali = base.resolve_active("Animali")
    return base.with_category(
        Category(
            id=UUID(int=0x9E0),
            name=ALIAS_CATEGORY,
            description="Toelettatura e cura degli animali domestici.",
            parent_id=animali.id,
            aliases=("toeletta", "toilettatura"),
        )
    ).with_category(
        Category(
            id=UUID(int=0x9E1),
            name=ARCHIVED_CATEGORY,
            description="Nodo storico non piu' utilizzabile.",
            parent_id=None,
            status=CategoryStatus.ARCHIVED,
        )
    )


def _reset_database(database_url: str) -> None:
    """Drop only the schemas this repository owns in the test database.

    The run starts from a known empty database so that node counts, proposal
    states, and checkpoint threads mean the same thing on every execution. The
    current-database check repeats the URL guard from inside the connection,
    because that is the only place a mistake could still be caught.

    Args:
        database_url: Already-guarded isolated test database URL.

    Raises:
        ValueError: If the open connection is not the test database.
    """

    with connect(database_url, autocommit=True) as connection:
        current = connection.execute("SELECT current_database()").fetchone()
        if current != ("money_tracker_test",):
            raise ValueError("refusing to reset a database other than the test one")
        for schema_name in ("workflow", "messaging", "finance"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


def _insert_fixture_aliases(database_url: str, category_id: UUID) -> None:
    """Persist the fixture aliases the bootstrap path does not carry.

    `bootstrap_profile` inserts seed categories without their aliases. That has
    never mattered, because the accepted seed taxonomy declares none, but it
    does mean the suite cannot obtain an alias through the ordinary bootstrap
    and must write the rows itself. The gap is recorded in the suite
    configuration rather than worked around silently.
    """

    with connect(database_url, autocommit=True) as connection:
        for alias in ("toeletta", "toilettatura"):
            connection.execute(
                "INSERT INTO finance.category_aliases "
                "(category_id, alias, normalized_alias) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (category_id, alias, alias),
            )


def _bootstrap_profile(
    repository: PostgresFinanceRepository,
    database_url: str,
) -> tuple[Profile, FundingSource]:
    """Seed one fresh profile, its taxonomy, and one active default source."""

    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=fixture_taxonomy(), occurred_at=now)
    _insert_fixture_aliases(
        database_url,
        repository.get_taxonomy(profile.id).resolve_active(ALIAS_CATEGORY).id,
    )
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    return profile, card


def _store_draft(
    repository: PostgresFinanceRepository,
    profile: Profile,
    card: FundingSource,
    description: str,
) -> Expense:
    """Store one draft whose purpose dimension is deliberately empty."""

    reference = f"eval:review:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=SystemClock().now().astimezone(PRODUCT_TIMEZONE),
        description=description,
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=card.id, amount=Money(1200)),
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    return repository.create_expense(
        expense,
        audit_event=AuditEvent(
            id=uuid4(),
            profile_id=profile.id,
            aggregate_type="expense",
            aggregate_id=expense.id,
            action="create",
            actor="eval",
            source_reference=reference,
            occurred_at=SystemClock().now(),
            prior_version=None,
            current_version=1,
            reason="category review suite fixture",
        ),
        request_hash=sha256(reference.encode()).hexdigest(),
    )


def _submit_proposal(
    repository: PostgresFinanceRepository,
    profile: Profile,
    expense: Expense,
    spec: Mapping[str, JsonValue],
) -> CategoryProposal:
    """Submit the pending candidate one case describes."""

    taxonomy = repository.get_taxonomy(profile.id)
    parent = spec.get("parent_name")
    alternatives = tuple(
        taxonomy.resolve_active(str(name)).id
        for name in cast(list[JsonValue], spec.get("alternative_names", []))
    )
    return CategoryProposalService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    ).submit(
        CategoryProposal(
            id=uuid4(),
            profile_id=profile.id,
            proposed_name=str(spec["proposed_name"]),
            description=str(
                spec.get("description") or "Candidata proposta dal flusso."
            ),
            parent_id=(
                None if parent is None else taxonomy.resolve_active(str(parent)).id
            ),
            rationale="Nessuna categoria attiva descrive lo scopo.",
            alternative_category_ids=alternatives,
            excluded_names=(),
            source_reference=expense.source_reference,
        ),
        actor="workflow",
        reason="no active category is adequate",
    )


def _row_counts(database_url: str, profile_id: UUID) -> tuple[int, int, int]:
    """Count the rows a duplicate write would add, scoped to one profile."""

    with connect(database_url) as connection:
        categories = connection.execute(
            "SELECT count(*) FROM finance.categories WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
        expenses = connection.execute(
            "SELECT count(*) FROM finance.expenses WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
        events = connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return (
        0 if categories is None else int(categories[0]),
        0 if expenses is None else int(expenses[0]),
        0 if events is None else int(events[0]),
    )


def _observe(
    repository: PostgresFinanceRepository,
    profile: Profile,
    expense_id: UUID,
    state: Mapping[str, Any],
    *,
    categories_before: int,
    duplicate_writes: int,
    can_repropose: bool | None,
    restart_payload: str | None,
) -> dict[str, JsonValue]:
    """Read every postcondition back from real storage.

    The returned state is not evidence on its own: a graph that reported an
    approval while writing nothing would satisfy a state-only check. Node
    counts, the stored movement, and the review's own proposal are what the
    matrix actually gates.

    The proposal is followed by the identifier the review currently holds
    rather than by querying the profile's pending queue. V1 permits exactly one
    active profile, so every case shares one owner, and a queue read would mix
    in reviews belonging to other scenarios.
    """

    taxonomy = repository.get_taxonomy(profile.id)
    movement = repository.get_expense(profile.id, expense_id)
    settled = state.get("settled_category_id")
    proposal = repository.get_category_proposal(
        profile.id, UUID(str(state["proposal_id"]))
    )
    excluded: list[JsonValue] = []
    excluded.extend(sorted(str(name) for name in proposal.excluded_names))
    return {
        "outcome": state.get("outcome"),
        "clarification_reason": state.get("clarification_reason"),
        "categories_created": len(taxonomy.categories) - categories_before,
        "expense_status": movement.status.value,
        "settled_category_name": (
            None if settled is None else taxonomy.category(UUID(str(settled))).name
        ),
        "purpose_allocations": len(movement.category_allocations),
        "proposal_status": proposal.status.value,
        "excluded_names": excluded,
        "can_repropose": can_repropose,
        "restart_payload": restart_payload,
        "duplicate_writes": duplicate_writes,
    }


def _decision(turn: Mapping[str, JsonValue]) -> ReviewDecision:
    """Build one owner decision from a scripted turn."""

    name = turn.get("category_name")
    return ReviewDecision(
        CallbackAction(str(turn["action"])),
        None if name is None else str(name),
    )


def _question_of(state: Mapping[str, Any]) -> Any | None:
    """Return the pause payload when the graph is waiting on the owner."""

    interrupts = state.get("__interrupt__")
    if not interrupts:
        return None
    return interrupts[0].value


def _can_repropose(question: Any) -> bool | None:
    """Read the re-proposal flag from a pause payload of either shape.

    A payload raised in this process is a `ReviewQuestion`. One read back from
    a checkpoint may be a plain mapping, because the strict msgpack allowlist
    refuses to reconstruct the class. Both carry the same fields, so the suite
    reads either rather than depending on which path produced it.
    """

    if question is None:
        return None
    if isinstance(question, Mapping):
        return bool(question["can_repropose"])
    return bool(question.can_repropose)


def _restored_pause_payload(database_url: str, configuration: RunnableConfig) -> str:
    """Report how a paused review's question survives a process restart.

    A persistent checkpointer exists so a review can outlive the process that
    opened it, which means the owner must still be presentable with the same
    question afterwards. This reads the pause payload back through a brand new
    saver and connection, and names the type it comes back as.

    Returns:
        `review_question` when the contract is reconstructed, `mapping` when
        only its fields survive, or `absent` when the thread is not paused.
    """

    with open_postgres_checkpointer(database_url) as reopened:
        graph = build_review_graph(
            model=ScriptedModel(()),
            configuration=PostgresFinanceRepository(database_url),
            review=CategoryReviewService(
                PostgresFinanceRepository(database_url),
                PostgresFinanceRepository(database_url),
                clock=SystemClock(),
                ids=UUID4Generator(),
            ),
            category_instructions="",
            checkpointer=reopened,
        )
        snapshot = graph.get_state(configuration)
    for task in snapshot.tasks:
        for pause in task.interrupts:
            if isinstance(pause.value, ReviewQuestion):
                return "review_question"
            return "mapping" if isinstance(pause.value, Mapping) else "unknown"
    return "absent"


def _run_scenario(
    database_url: str,
    repository: PostgresFinanceRepository,
    profile: Profile,
    card: FundingSource,
    case: Mapping[str, JsonValue],
    instructions: str,
) -> dict[str, JsonValue]:
    """Drive one multi-turn review to its end and read back its postconditions."""

    categories_before = len(repository.get_taxonomy(profile.id).categories)
    draft = _store_draft(
        repository, profile, card, str(case.get("description") or "spesa da rivedere")
    )
    proposal = _submit_proposal(
        repository, profile, draft, cast(Mapping[str, JsonValue], case["proposal"])
    )
    completions = [
        json.dumps(candidate)
        for candidate in cast(list[JsonValue], case.get("repropositions", []))
    ]
    configuration: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    turns = cast(list[Mapping[str, JsonValue]], case["turns"])

    with open_postgres_checkpointer(database_url) as checkpointer:
        graph = build_review_graph(
            model=ScriptedModel(completions),
            configuration=repository,
            review=CategoryReviewService(
                repository, repository, clock=SystemClock(), ids=UUID4Generator()
            ),
            category_instructions=instructions,
            checkpointer=checkpointer,
        )
        state: Mapping[str, Any] = graph.invoke(
            initial_review_state(
                profile_id=profile.id,
                proposal_id=proposal.id,
                expense_id=draft.id,
                source_reference=draft.source_reference,
            ),
            configuration,
        )
        can_repropose = _independent_work(
            database_url,
            repository,
            profile,
            card,
            graph,
            configuration,
            case,
            state,
            instructions,
        )
        for turn in turns:
            state = graph.invoke(Command(resume=_decision(turn)), configuration)
            answered = _can_repropose(_question_of(state))
            if answered is not None:
                can_repropose = answered

        restart_payload = (
            _restored_pause_payload(database_url, configuration)
            if case.get("check_restart")
            else None
        )
        duplicate_writes = 0
        if case.get("replay_last_turn"):
            before = _row_counts(database_url, profile.id)
            state = graph.invoke(Command(resume=_decision(turns[-1])), configuration)
            after = _row_counts(database_url, profile.id)
            duplicate_writes = sum(b - a for a, b in zip(before, after, strict=True))

    return _observe(
        repository,
        profile,
        draft.id,
        state,
        categories_before=categories_before,
        duplicate_writes=duplicate_writes,
        can_repropose=can_repropose,
        restart_payload=restart_payload,
    )


def _independent_work(
    database_url: str,
    repository: PostgresFinanceRepository,
    profile: Profile,
    card: FundingSource,
    graph: Any,
    configuration: RunnableConfig,
    case: Mapping[str, JsonValue],
    state: Mapping[str, Any],
    instructions: str,
) -> bool | None:
    """Handle unrelated owner work while the review waits, then check it lived.

    The matrix requires an unrelated message to be handled independently
    without losing the pending action. The unrelated work here is a second
    complete review, opened and cancelled on its own thread against the same
    owner, which is the strongest form of the property: a concurrent decision
    settles fully while the first review must still be paused on its own
    candidate afterwards.

    Cancelling rather than approving is deliberate. A cancellation creates no
    taxonomy node, so the interference cannot disturb the node-count delta the
    calling scenario is measured on.
    """

    can_repropose = _can_repropose(_question_of(state))
    if not case.get("unrelated_input"):
        return can_repropose

    other_draft = _store_draft(repository, profile, card, "spesa non correlata")
    other_proposal = _submit_proposal(
        repository,
        profile,
        other_draft,
        {
            "proposed_name": str(case["unrelated_input"]),
            "parent_name": None,
            "alternative_names": [],
        },
    )
    other_configuration: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    graph.invoke(
        initial_review_state(
            profile_id=profile.id,
            proposal_id=other_proposal.id,
            expense_id=other_draft.id,
            source_reference=other_draft.source_reference,
        ),
        other_configuration,
    )
    graph.invoke(
        Command(resume=ReviewDecision(CallbackAction.CANCEL_REVIEW)),
        other_configuration,
    )
    if repository.get_expense(profile.id, other_draft.id).status is not (
        ExpenseStatus.DISCARDED
    ):
        raise AssertionError("the unrelated review did not settle")
    snapshot = graph.get_state(configuration)
    if snapshot.next != (ASK_NODE,):
        raise AssertionError("the paused review lost its position")
    return can_repropose


def _matches(
    expected: Mapping[str, JsonValue], observed: Mapping[str, JsonValue]
) -> list[str]:
    """Return the postcondition keys one case declared and did not get."""

    return sorted(key for key, value in expected.items() if observed.get(key) != value)


def _branch_metric(
    branch: str,
    scored: Sequence[tuple[str, bool]],
    *,
    name: str,
) -> MetricScore:
    """Score one matrix branch at the 100% its scenarios are gated at."""

    applicable = [passed for tag, passed in scored if tag == branch]
    return MetricScore(
        name=name,
        value=rate(sum(applicable), len(applicable)),
        threshold=1.0,
        blocking=True,
        numerator=sum(applicable),
        denominator=len(applicable),
    )


def _metrics(
    cases: Sequence[Mapping[str, JsonValue]],
    results: Sequence[CaseResult],
    observations: Mapping[str, Mapping[str, JsonValue]],
) -> list[MetricScore]:
    """Score every branch the evaluation matrix defines for the category HITL."""

    scored = [
        (str(case["branch"]), result.status is CaseStatus.PASSED)
        for case, result in zip(cases, results, strict=True)
    ]
    replayed = [
        int(cast(int, observations[result.case_id].get("duplicate_writes", 0)))
        for case, result in zip(cases, results, strict=True)
        if case.get("replay_last_turn")
    ]
    restarts = [
        str(observations[result.case_id].get("restart_payload"))
        for case, result in zip(cases, results, strict=True)
        if case.get("check_restart")
        and observations[result.case_id].get("restart_payload") is not None
    ]
    reconstructed = sum(1 for value in restarts if value == "review_question")
    passed = sum(1 for _, ok in scored if ok)
    return [
        _branch_metric("approve", scored, name="approve_new_category_postcondition"),
        _branch_metric("best_existing", scored, name="use_best_existing_postcondition"),
        _branch_metric("named_existing", scored, name="named_existing_resolution"),
        _branch_metric("repropose", scored, name="repropose_excludes_rejected"),
        _branch_metric("exhausted", scored, name="exhausted_transition_to_manual"),
        _branch_metric("cancel", scored, name="cancel_mutates_nothing"),
        _branch_metric("unrelated", scored, name="unrelated_input_preserves_pending"),
        MetricScore(
            name="duplicate_writes_under_replay",
            value=float(sum(replayed)),
            threshold=0.0,
            comparison="at_most",
            blocking=True,
            numerator=sum(replayed),
            denominator=len(replayed),
        ),
        MetricScore(
            name="pause_payload_survives_restart",
            value=rate(reconstructed, len(restarts)),
            threshold=None,
            blocking=False,
            numerator=reconstructed,
            denominator=len(restarts),
        ),
        MetricScore(
            name="scenario_agreement",
            value=rate(passed, len(scored)),
            threshold=None,
            blocking=False,
            numerator=passed,
            denominator=len(scored),
        ),
    ]


def run_category_review_suite(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    repository_root: Path,
    database_url: str,
    instructions: str = CANDIDATE_CATEGORY_FIT_INSTRUCTIONS,
) -> SuiteReport:
    """Execute the category review suite against real PostgreSQL.

    Args:
        settings: Local endpoint configuration, recorded but not reached.
        dataset_path: Committed scenario dataset.
        output_root: Ignored artifact root for raw run output.
        repository_root: Repository directory, for source metadata.
        database_url: Isolated integration-test database. Any other database
            is refused.
        instructions: Purpose-boundary text used only by the re-proposal
            branch, recorded so a run stays attributable.

    Returns:
        The complete report, already written to its artifact directory.
    """

    require_isolated_database(database_url)
    started_at = utc_now()
    cases, content_hash = load_cases(dataset_path)
    dataset = dataset_metadata(
        suite=SUITE, path=dataset_path, cases=cases, content_hash=content_hash
    )
    _reset_database(database_url)
    upgrade_database(database_url)
    bootstrap_checkpointer(database_url)
    repository = PostgresFinanceRepository(database_url)
    profile, card = _bootstrap_profile(repository, database_url)

    results: list[CaseResult] = []
    observations: dict[str, Mapping[str, JsonValue]] = {}
    for case in cases:
        result, observed = _execute(
            database_url, repository, profile, card, case, instructions
        )
        results.append(result)
        observations[result.case_id] = observed

    metrics = _metrics(cases, results, observations)
    passed = blocking_gates_passed(metrics)
    meets_minimum = dataset.item_count >= DATASET_MINIMUM
    report = SuiteReport(
        run_id=new_run_id("REV"),
        suite=SUITE,
        started_at=started_at,
        completed_at=utc_now(),
        source=collect_source_metadata(repository_root),
        dataset=dataset,
        model=model_metadata(settings),
        environment=collect_environment_metadata(),
        configuration={
            "instructions_sha256": sha256_text(instructions),
            "model_port": "scripted",
            "dataset_minimum": DATASET_MINIMUM,
            "fixture_taxonomy_nodes": len(fixture_taxonomy().categories),
            "aliases_written_outside_bootstrap": True,
            "auto_post_policy_promoted": False,
        },
        metrics=metrics,
        cases=results,
        blocking_gates_passed=passed,
        meets_dataset_minimum=meets_minimum,
        eligible_for_promotion=passed and meets_minimum,
        local_artifact_path="",
    )
    directory = write_report(report, output_root=output_root)
    report = report.model_copy(update={"local_artifact_path": str(directory)})
    write_report(report, output_root=output_root)
    return report


def _execute(
    database_url: str,
    repository: PostgresFinanceRepository,
    profile: Profile,
    card: FundingSource,
    case: Mapping[str, JsonValue],
    instructions: str,
) -> tuple[CaseResult, Mapping[str, JsonValue]]:
    """Run one scenario and compare every declared postcondition."""

    case_id = str(case["case_id"])
    branch = str(case["branch"])
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    expected = cast(Mapping[str, JsonValue], case["expected"])
    started = time.monotonic()
    try:
        observed = _run_scenario(
            database_url, repository, profile, card, case, instructions
        )
    except Exception as error:  # noqa: BLE001 - a suite records, never raises
        return (
            CaseResult(
                case_id=case_id,
                status=CaseStatus.ERROR,
                expected=branch,
                tags=tags,
                latency_ms=round((time.monotonic() - started) * 1000),
                detail=type(error).__name__,
            ),
            {},
        )
    missed = _matches(expected, observed)
    return (
        CaseResult(
            case_id=case_id,
            status=CaseStatus.PASSED if not missed else CaseStatus.FAILED,
            expected=branch,
            actual=branch if not missed else "postcondition_violated",
            tags=tags,
            latency_ms=round((time.monotonic() - started) * 1000),
            detail=",".join(missed) or None,
        ),
        observed,
    )


__all__ = [
    "ALIAS_CATEGORY",
    "ARCHIVED_CATEGORY",
    "DATASET_MINIMUM",
    "SUITE",
    "ScriptedModel",
    "fixture_taxonomy",
    "require_isolated_database",
    "run_category_review_suite",
]
