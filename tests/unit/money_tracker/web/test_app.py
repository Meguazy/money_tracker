"""Tests for observable behavior of the local web adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from money_tracker.application import (
    BudgetPlan,
    BudgetPlanEntry,
    CategoryReviewQueue,
    MonthlyOverview,
    PendingCategoryReview,
    PeriodSplit,
    TransactionRegister,
)
from money_tracker.application.ports import IdGenerator
from money_tracker.domain import (
    BalanceMode,
    BudgetStatus,
    CategoryAllocation,
    CategoryProposal,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingClassSplit,
    FundingSource,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    ReceiptMetadata,
    ReceiptStatus,
    YearMonth,
    initial_taxonomy,
)
from money_tracker.web import WebSettings, create_app
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    BudgetWriter,
    CategoryReviewMutationAdapter,
    CategoryReviewWriter,
    ExpenseMutationAdapter,
    ExpenseWriter,
    ReceiptReviewMutationAdapter,
    ReceiptReviewWriter,
)

PROFILE_ID = uuid4()
PERIOD = YearMonth(2026, 9)
ORIGIN = "http://localhost:8000"
ROME = ZoneInfo("Europe/Rome")
EXPENSE_ID = UUID("00000000-0000-0000-0000-000000000301")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000201")
RECEIPT_LINE_ID = UUID("00000000-0000-0000-0000-000000000401")
BUDGET_ID = UUID("00000000-0000-0000-0000-000000000501")
RECEIPT_ID = UUID("00000000-0000-0000-0000-000000000601")
RECEIPT_DIGEST = "9f" * 32


@dataclass
class OverviewReader:
    """Small in-memory implementation of the read-only web port."""

    requested_profile: UUID | None = None
    requested_period: YearMonth | None = None

    def monthly_overview(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> MonthlyOverview:
        """Return fixed exact facts and record trusted scoping inputs."""

        self.requested_profile = profile_id
        self.requested_period = period
        source = FundingSource(
            id=uuid4(),
            profile_id=profile_id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        )
        return MonthlyOverview(
            period=period,
            posted_total=Money(5000),
            split=PeriodSplit(
                period=period,
                split=FundingClassSplit(Money(2000), Money(3000), Money(0)),
                sources=((source, Money(2000)),),
            ),
            budgets=(
                (
                    "Spesa alimentare",
                    BudgetStatus(period, Money(8000), Money(5000), Money(3000)),
                ),
            ),
            unbudgeted_category_names=("Abbonamenti",),
        )

    def transaction_register(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> TransactionRegister:
        """Return one split movement over the real baseline taxonomy."""

        self.requested_profile = profile_id
        self.requested_period = period
        taxonomy = initial_taxonomy()
        food = taxonomy.resolve_active("Spesa alimentare")
        home = taxonomy.resolve_active("Prodotti casa, arredo e manutenzione")
        source = FundingSource(
            id=SOURCE_ID,
            profile_id=profile_id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        )
        return TransactionRegister(
            period=period,
            expenses=(
                Expense(
                    id=EXPENSE_ID,
                    profile_id=profile_id,
                    amount=Money(5000),
                    occurred_at=datetime(2026, 9, 12, 10, 30, tzinfo=ROME),
                    description="Spesa mista",
                    merchant="Mercato",
                    status=ExpenseStatus.DRAFT,
                    category_allocations=(
                        CategoryAllocation(
                            category_id=food.id,
                            amount=Money(3000),
                            provenance="workflow-category-fit:v6",
                            evidence="pesche",
                            receipt_line_id=RECEIPT_LINE_ID,
                        ),
                        CategoryAllocation(
                            category_id=home.id,
                            amount=Money(2000),
                            provenance="owner:web:v1",
                            owner_override=True,
                        ),
                    ),
                    payment_allocations=(
                        PaymentAllocation(
                            funding_source_id=source.id,
                            amount=Money(5000),
                        ),
                    ),
                    source_reference="web:unit",
                    idempotency_key="web:unit",
                ),
            ),
            taxonomy=taxonomy,
            funding_sources=(source,),
        )

    def budget_plan(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> BudgetPlan:
        """Return one configured budget and the categories still available."""

        self.requested_profile = profile_id
        self.requested_period = period
        taxonomy = initial_taxonomy()
        food = taxonomy.resolve_active("Spesa alimentare")
        return BudgetPlan(
            period=period,
            entries=(
                BudgetPlanEntry(
                    budget=MonthlyBudget(
                        id=BUDGET_ID,
                        profile_id=profile_id,
                        category_id=food.id,
                        recurring_target=Money(40000),
                        overrides=((YearMonth(2026, 12), Money(60000)),),
                        version=3,
                    ),
                    category_name=food.name,
                    status=BudgetStatus(
                        period,
                        Money(40000),
                        Money(45000),
                        Money(-5000),
                    ),
                ),
            ),
            budgetable_categories=(taxonomy.resolve_active("Cura personale"),),
        )


@dataclass
class RecordingBudgetWriter:
    """In-memory budget writer that records the translated command."""

    created: MonthlyBudget | None = None
    replaced: tuple[UUID, int, Money, tuple[tuple[YearMonth, Money], ...]] | None = None
    command_keys: list[str] = field(default_factory=list)

    def create_budget(
        self,
        budget: MonthlyBudget,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Record the aggregate the adapter built from a browser request."""

        del actor, reason
        self.created = budget
        self.command_keys.append(idempotency_key)
        return budget

    def replace_budget(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        recurring_target: Money,
        overrides: tuple[tuple[YearMonth, Money], ...],
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Record the replacement plan and return the advanced aggregate."""

        del actor, reason
        self.replaced = (category_id, expected_version, recurring_target, overrides)
        self.command_keys.append(idempotency_key)
        return MonthlyBudget(
            id=BUDGET_ID,
            profile_id=profile_id,
            category_id=category_id,
            recurring_target=recurring_target,
            overrides=overrides,
            version=expected_version + 1,
        )


@dataclass
class RecordingCategoryReviewWriter:
    """In-memory review writer recording only browser-selected decisions."""

    queue: CategoryReviewQueue
    actions: list[tuple[str, UUID, str | None]] = field(default_factory=list)

    def pending_reviews(self, *, profile_id: UUID) -> CategoryReviewQueue:
        """Return the fixed trusted-profile review queue."""

        assert profile_id == PROFILE_ID
        return self.queue

    def review_expense(self, *, profile_id: UUID, proposal_id: UUID) -> Expense:
        """Resolve the movement without accepting it from the request."""

        assert profile_id == PROFILE_ID
        review = next(
            item for item in self.queue.reviews if item.proposal.id == proposal_id
        )
        return review.expense

    def approve(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Record approval and return the resulting posted movement."""

        del profile_id, actor, reason
        self.actions.append(("approve", proposal_id, None))
        expense = self.review_expense(profile_id=PROFILE_ID, proposal_id=proposal_id)
        assert expense.id == expense_id
        return replace(
            expense, status=ExpenseStatus.POSTED, version=expense.version + 2
        )

    def use_existing(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        category_name: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Record reuse and return the resulting posted movement."""

        del profile_id, actor, reason
        self.actions.append(("use_existing", proposal_id, category_name))
        expense = self.review_expense(profile_id=PROFILE_ID, proposal_id=proposal_id)
        assert expense.id == expense_id
        return replace(
            expense, status=ExpenseStatus.POSTED, version=expense.version + 2
        )

    def cancel(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Record cancellation and return the discarded movement."""

        del profile_id, actor, reason
        self.actions.append(("cancel", proposal_id, None))
        expense = self.review_expense(profile_id=PROFILE_ID, proposal_id=proposal_id)
        assert expense.id == expense_id
        return replace(
            expense, status=ExpenseStatus.DISCARDED, version=expense.version + 1
        )


def _category_review_writer() -> RecordingCategoryReviewWriter:
    """Build one pending proposal over the real baseline taxonomy."""

    taxonomy = initial_taxonomy()
    expense = (
        OverviewReader()
        .transaction_register(profile_id=PROFILE_ID, period=PERIOD)
        .expenses[0]
    )
    proposal = CategoryProposal(
        id=uuid4(),
        profile_id=PROFILE_ID,
        proposed_name="Cura del giardino",
        description="Piante, terriccio e attrezzi per il giardino.",
        parent_id=taxonomy.resolve_active("Casa e utenze").id,
        rationale="Le categorie attive non distinguono questa finalità.",
        alternative_category_ids=(taxonomy.resolve_active("Acquisti personali").id,),
        excluded_names=("Giardinaggio",),
        source_reference=expense.source_reference,
    )
    return RecordingCategoryReviewWriter(
        CategoryReviewQueue(
            reviews=(PendingCategoryReview(proposal, expense),),
            taxonomy=taxonomy,
        )
    )


@dataclass
class RecordingReceiptReviewWriter:
    """In-memory receipt reader and review writer without a media store."""

    transitions: list[tuple[str, UUID, int]] = field(default_factory=list)
    status: ReceiptStatus = ReceiptStatus.STORED
    version: int = 1

    def _metadata(self) -> ReceiptMetadata:
        """Build the single retained receipt at its current review state."""

        return ReceiptMetadata(
            id=RECEIPT_ID,
            profile_id=PROFILE_ID,
            content_sha256=RECEIPT_DIGEST,
            media_type="image/jpeg",
            byte_size=204800,
            stored_at=datetime(2026, 9, 12, 18, 5, tzinfo=UTC),
            source_reference="local-admin:receipt:unit",
            status=self.status,
            original_filename="scontrino-mercato.jpg",
            version=self.version,
        )

    def stored_receipts(self, *, profile_id: UUID) -> tuple[ReceiptMetadata, ...]:
        """Return the fixed archive for the trusted profile only."""

        assert profile_id == PROFILE_ID
        return (self._metadata(),)

    def mark_needs_review(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Record the requested transition and advance the review state."""

        del profile_id, actor, reason
        self.transitions.append(("mark_needs_review", receipt_id, expected_version))
        self.status = ReceiptStatus.NEEDS_REVIEW
        self.version = expected_version + 1
        return self._metadata()

    def approve(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Record explicit approval and advance the review state."""

        del profile_id, actor, reason
        self.transitions.append(("approve", receipt_id, expected_version))
        self.status = ReceiptStatus.APPROVED
        self.version = expected_version + 1
        return self._metadata()


class FixedIds:
    """Stable identifier source for adapter composition in boundary tests."""

    def new(self) -> UUID:
        """Return a deterministic valid identifier."""

        return UUID("00000000-0000-0000-0000-000000000999")


def _app(
    reader: OverviewReader | None = None,
    *,
    static_dir: Path | None = None,
    ready: bool = True,
    budget_writer: BudgetWriter | None = None,
    category_review_writer: CategoryReviewWriter | None = None,
    receipt_writer: ReceiptReviewWriter | None = None,
) -> FastAPI:
    """Compose an application with real middleware and an in-memory read port."""

    return create_app(
        settings=WebSettings(static_dir=static_dir),
        profile_id=PROFILE_ID,
        reporting=reader or OverviewReader(),
        mutations=ExpenseMutationAdapter(
            cast(ExpenseWriter, object()),
            ids=cast(IdGenerator, FixedIds()),
        ),
        budgets=BudgetMutationAdapter(
            budget_writer or cast(BudgetWriter, object()),
            ids=cast(IdGenerator, FixedIds()),
        ),
        category_reviews=CategoryReviewMutationAdapter(
            category_review_writer or cast(CategoryReviewWriter, object())
        ),
        receipts=ReceiptReviewMutationAdapter(
            receipt_writer or RecordingReceiptReviewWriter()
        ),
        readiness_check=lambda: ready,
    )


def _client(app: FastAPI) -> TestClient:
    """Use an allowed authority so only intentional policy failures vary."""

    return TestClient(app, base_url=ORIGIN)


def test_monthly_overview_returns_exact_minor_units_in_the_trusted_profile() -> None:
    """Expose deterministic facts without accepting owner identity from input."""

    reader = OverviewReader()

    response = _client(_app(reader)).get("/api/v1/overview/monthly?period=2026-09")

    assert response.status_code == 200
    assert response.json() == {
        "period": {"year": 2026, "month": 9},
        "posted_total": {"minor_units": 5000, "currency": "EUR"},
        "funding_split": {
            "personal_funds": {"minor_units": 2000, "currency": "EUR"},
            "benefit": {"minor_units": 3000, "currency": "EUR"},
            "other": {"minor_units": 0, "currency": "EUR"},
        },
        "funding_sources": [
            {
                "name": "Carta",
                "funding_class": "personal_funds",
                "amount": {"minor_units": 2000, "currency": "EUR"},
            }
        ],
        "budgets": [
            {
                "category_name": "Spesa alimentare",
                "target": {"minor_units": 8000, "currency": "EUR"},
                "posted_spend": {"minor_units": 5000, "currency": "EUR"},
                "remaining": {"minor_units": 3000, "currency": "EUR"},
            }
        ],
        "unbudgeted_categories": ["Abbonamenti"],
    }
    assert reader.requested_profile == PROFILE_ID
    assert reader.requested_period == PERIOD
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["content-security-policy"].startswith("default-src")
    assert response.headers["cache-control"] == "no-store"


def test_transaction_register_uses_trusted_scope_and_safe_references() -> None:
    """Expose editor references without returning profile or channel metadata."""

    reader = OverviewReader()

    response = _client(_app(reader)).get(
        "/api/v1/transactions?period=2026-09",
    )

    assert response.status_code == 200
    assert response.json()["period"] == {"year": 2026, "month": 9}
    assert response.json()["categories"]
    assert [source["name"] for source in response.json()["funding_sources"]] == [
        "Carta"
    ]
    assert reader.requested_profile == PROFILE_ID
    assert reader.requested_period == PERIOD
    assert "profile_id" not in response.text
    assert "source_reference" not in response.text


def test_split_allocations_keep_exact_cents_and_their_receipt_line() -> None:
    """Give the editor every allocation with the metadata a correction must keep."""

    taxonomy = initial_taxonomy()
    home = taxonomy.resolve_active("Prodotti casa, arredo e manutenzione")

    response = _client(_app()).get("/api/v1/transactions?period=2026-09")

    transaction = response.json()["transactions"][0]
    assert transaction["amount"] == {"minor_units": 5000, "currency": "EUR"}
    assert transaction["category_allocations"] == [
        {
            "category_id": str(taxonomy.resolve_active("Spesa alimentare").id),
            "category_name": "Spesa alimentare",
            "amount": {"minor_units": 3000, "currency": "EUR"},
            "provenance": "workflow-category-fit:v6",
            "evidence": "pesche",
            "owner_override": False,
            "receipt_line_id": str(RECEIPT_LINE_ID),
        },
        {
            "category_id": str(home.id),
            "category_name": "Prodotti casa, arredo e manutenzione",
            "amount": {"minor_units": 2000, "currency": "EUR"},
            "provenance": "owner:web:v1",
            "evidence": None,
            "owner_override": True,
            "receipt_line_id": None,
        },
    ]
    assert transaction["payment_allocations"] == [
        {
            "funding_source_id": str(SOURCE_ID),
            "funding_source_name": "Carta",
            "amount": {"minor_units": 5000, "currency": "EUR"},
        }
    ]


def test_category_proposals_expose_review_facts_without_workflow_identity() -> None:
    """Show the candidate, movement, and deterministic active choices safely."""

    writer = _category_review_writer()

    response = _client(_app(category_review_writer=writer)).get(
        "/api/v1/category-proposals"
    )

    assert response.status_code == 200
    proposal = response.json()["proposals"][0]
    assert proposal["id"] == str(writer.queue.reviews[0].proposal.id)
    assert proposal["proposed_name"] == "Cura del giardino"
    assert proposal["parent_name"] == "Casa e utenze"
    assert proposal["alternative_categories"][0]["name"] == "Acquisti personali"
    assert proposal["excluded_names"] == ["Giardinaggio"]
    assert proposal["can_repropose"] is True
    assert proposal["expense"]["id"] == str(writer.queue.reviews[0].expense.id)
    assert proposal["expense"]["amount"] == {
        "minor_units": 5000,
        "currency": "EUR",
    }
    assert "source_reference" not in response.text
    assert "profile_id" not in response.text


def test_category_approval_uses_only_the_proposal_wide_command_identity() -> None:
    """Resolve movement identity server-side and replay through the service key."""

    writer = _category_review_writer()
    proposal_id = writer.queue.reviews[0].proposal.id
    client = _client(_app(category_review_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        f"/api/v1/category-proposals/{proposal_id}/approve",
        json={},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"category-review:{proposal_id}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(writer.queue.reviews[0].expense.id),
        "status": "posted",
        "version": 3,
    }
    assert writer.actions == [("approve", proposal_id, None)]


def test_category_reuse_validates_strict_json_and_the_proposal_key() -> None:
    """Reject browser-controlled identity and pass only an active display name."""

    writer = _category_review_writer()
    proposal_id = writer.queue.reviews[0].proposal.id
    client = _client(_app(category_review_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    headers = {
        "origin": ORIGIN,
        "x-csrf-token": issued.json()["token"],
        "Idempotency-Key": f"category-review:{proposal_id}",
    }

    invalid = client.post(
        f"/api/v1/category-proposals/{proposal_id}/use-existing",
        json={
            "category_name": "Acquisti personali",
            "expense_id": str(uuid4()),
        },
        headers=headers,
    )
    wrong_key = client.post(
        f"/api/v1/category-proposals/{proposal_id}/use-existing",
        json={"category_name": "Acquisti personali"},
        headers={**headers, "Idempotency-Key": "different-key"},
    )
    accepted = client.post(
        f"/api/v1/category-proposals/{proposal_id}/use-existing",
        json={"category_name": "Acquisti personali"},
        headers=headers,
    )

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "request_invalid"
    assert wrong_key.status_code == 409
    assert wrong_key.json()["code"] == "category_review_conflict"
    assert accepted.status_code == 200
    assert writer.actions == [("use_existing", proposal_id, "Acquisti personali")]


def test_invalid_period_returns_a_bounded_problem() -> None:
    """Reject ambiguous dates without invoking the application reader."""

    reader = OverviewReader()
    response = _client(_app(reader)).get("/api/v1/overview/monthly?period=09-2026")

    assert response.status_code == 422
    assert response.json()["code"] == "period_invalid"
    assert reader.requested_period is None


def test_health_distinguishes_liveness_from_dependency_readiness() -> None:
    """Keep process health observable while PostgreSQL is unavailable."""

    client = _client(_app(ready=False))

    assert client.get("/api/v1/health/live").status_code == 200
    readiness = client.get("/api/v1/health/ready")
    assert readiness.status_code == 503
    assert readiness.json()["code"] == "database_unavailable"


def test_host_and_origin_allowlists_block_browser_boundary_bypass() -> None:
    """Reject DNS rebinding and cross-origin reads before routing."""

    client = _client(_app())

    bad_host = client.get("/api/v1/health/live", headers={"host": "evil.test"})
    bad_origin = client.get(
        "/api/v1/health/live",
        headers={"origin": "http://evil.test"},
    )

    assert bad_host.status_code == 403
    assert bad_host.json()["code"] == "host_not_allowed"
    assert bad_origin.status_code == 403
    assert bad_origin.json()["code"] == "origin_not_allowed"


def test_unsafe_methods_require_matching_origin_cookie_and_csrf_header() -> None:
    """Enforce the double-submit contract even on later mutation routes."""

    app = _app()

    @app.post("/api/v1/test-mutation")
    def mutation() -> dict[str, bool]:
        """Provide a test-only unsafe route behind production middleware."""

        return {"accepted": True}

    client = _client(app)
    rejected = client.post(
        "/api/v1/test-mutation",
        headers={"origin": ORIGIN},
    )
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    accepted = client.post(
        "/api/v1/test-mutation",
        headers={"origin": ORIGIN, "x-csrf-token": issued.json()["token"]},
    )

    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_validation_failed"
    assert issued.headers["cache-control"] == "no-store"
    assert accepted.json() == {"accepted": True}


def test_expense_mutation_rejects_unknown_json_without_adapter_dispatch() -> None:
    """Keep strict parser details behind a bounded owner-facing problem."""

    client = _client(_app())
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    response = client.post(
        "/api/v1/expenses",
        json={"unexpected": "field"},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-invalid-body",
        },
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "about:blank",
        "title": "Richiesta non valida",
        "status": 422,
        "detail": "Controlla i campi JSON della richiesta.",
        "code": "request_invalid",
        "instance": "/api/v1/expenses",
    }


def test_compiled_dashboard_is_served_from_the_api_origin(tmp_path: Path) -> None:
    """Serve one build while keeping source maps and arbitrary files private."""

    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<main>Money Tracker</main>")

    client = _client(_app(static_dir=static_dir))
    response = client.get("/")
    links = {
        route: client.get(route)
        for route in ("/movimenti", "/categorie", "/budget", "/scontrini")
    }

    assert response.status_code == 200
    assert "Money Tracker" in response.text
    assert [link.status_code for link in links.values()] == [200, 200, 200, 200]
    assert all(link.text == response.text for link in links.values())


def test_budget_plan_exposes_targets_overrides_and_the_correction_version() -> None:
    """Give the planner what a replacement must quote, and nothing more."""

    taxonomy = initial_taxonomy()
    food = taxonomy.resolve_active("Spesa alimentare")

    response = _client(_app()).get("/api/v1/budgets?period=2026-09")

    assert response.status_code == 200
    assert response.json()["period"] == {"year": 2026, "month": 9}
    assert response.json()["entries"] == [
        {
            "category_id": str(food.id),
            "category_name": "Spesa alimentare",
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "overrides": [
                {
                    "period": {"year": 2026, "month": 12},
                    "target": {"minor_units": 60000, "currency": "EUR"},
                }
            ],
            "target": {"minor_units": 40000, "currency": "EUR"},
            "posted_spend": {"minor_units": 45000, "currency": "EUR"},
            "remaining": {"minor_units": -5000, "currency": "EUR"},
            "version": 3,
        }
    ]
    assert [item["name"] for item in response.json()["budgetable_categories"]] == [
        "Cura personale"
    ]
    assert "profile_id" not in response.text
    assert str(BUDGET_ID) not in response.text


def test_budget_creation_injects_trusted_scope_and_exact_targets() -> None:
    """Build the aggregate from trusted context, never from request identity."""

    writer = RecordingBudgetWriter()
    taxonomy = initial_taxonomy()
    food = taxonomy.resolve_active("Spesa alimentare")
    client = _client(_app(budget_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        "/api/v1/budgets",
        json={
            "category_id": str(food.id),
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "overrides": [
                {
                    "period": {"year": 2026, "month": 12},
                    "target": {"minor_units": 60000, "currency": "EUR"},
                }
            ],
        },
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-budget-create",
        },
    )

    assert response.status_code == 201
    assert response.json() == {"category_id": str(food.id), "version": 1}
    assert writer.created is not None
    assert writer.created.profile_id == PROFILE_ID
    assert writer.created.recurring_target == Money(40000)
    assert writer.created.overrides == ((YearMonth(2026, 12), Money(60000)),)
    assert writer.command_keys == ["unit-budget-create"]


def test_budget_replacement_quotes_the_observed_version() -> None:
    """Send the complete plan and the version the browser actually saw."""

    writer = RecordingBudgetWriter()
    taxonomy = initial_taxonomy()
    food = taxonomy.resolve_active("Spesa alimentare")
    client = _client(_app(budget_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.put(
        f"/api/v1/budgets/{food.id}",
        json={
            "recurring_target": {"minor_units": 50000, "currency": "EUR"},
            "overrides": [],
            "expected_version": 3,
        },
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-budget-replace",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"category_id": str(food.id), "version": 4}
    assert writer.replaced == (food.id, 3, Money(50000), ())


def test_budget_mutation_rejects_a_profile_field_in_the_request_body() -> None:
    """Refuse owner identity from the browser instead of silently ignoring it."""

    writer = RecordingBudgetWriter()
    client = _client(_app(budget_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        "/api/v1/budgets",
        json={
            "category_id": str(uuid4()),
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "profile_id": str(uuid4()),
        },
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-budget-unknown-field",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"
    assert writer.created is None


def test_receipt_register_exposes_safe_facts_without_the_archive_key() -> None:
    """Give the owner display metadata and the version a decision must quote."""

    response = _client(_app()).get("/api/v1/receipts")

    assert response.status_code == 200
    assert response.json() == {
        "receipts": [
            {
                "id": str(RECEIPT_ID),
                "media_type": "image/jpeg",
                "byte_size": 204800,
                "stored_at": "2026-09-12T18:05:00Z",
                "status": "stored",
                "original_filename": "scontrino-mercato.jpg",
                "version": 1,
            }
        ]
    }
    assert RECEIPT_DIGEST not in response.text
    assert "profile_id" not in response.text
    assert "source_reference" not in response.text


def test_receipt_review_requires_the_transition_wide_command_key() -> None:
    """Bind each HTTP retry to the receipt and the version the page displayed."""

    writer = RecordingReceiptReviewWriter()
    client = _client(_app(receipt_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    refused = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/review",
        json={"expected_version": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}",
        },
    )
    accepted = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/review",
        json={"expected_version": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}:1",
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "receipt_conflict"
    assert accepted.status_code == 200
    assert accepted.json() == {
        "id": str(RECEIPT_ID),
        "status": "needs_review",
        "version": 2,
    }
    assert writer.transitions == [("mark_needs_review", RECEIPT_ID, 1)]


def test_receipt_approval_quotes_the_version_the_browser_observed() -> None:
    """Advance mandatory review only from the state the owner actually saw."""

    writer = RecordingReceiptReviewWriter(
        status=ReceiptStatus.NEEDS_REVIEW,
        version=2,
    )
    client = _client(_app(receipt_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/approve",
        json={"expected_version": 2},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}:2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(RECEIPT_ID),
        "status": "approved",
        "version": 3,
    }
    assert writer.transitions == [("approve", RECEIPT_ID, 2)]


def test_receipt_review_rejects_an_unknown_field_without_transitioning() -> None:
    """Refuse a body that names anything but the observed version."""

    writer = RecordingReceiptReviewWriter()
    client = _client(_app(receipt_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/approve",
        json={"expected_version": 1, "content_sha256": RECEIPT_DIGEST},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}:1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"
    assert writer.transitions == []
