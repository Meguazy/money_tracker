"""HTTP contract tests for the local read-only advice surface."""

from fastapi.testclient import TestClient

from money_tracker.web.advice import AdviceRun
from money_tracker.workflows import ReadOutcome, ReadReason, ReadTool
from tests.unit.money_tracker.web.test_app import (
    ORIGIN,
    PROFILE_ID,
    RecordingAdviceReader,
    _app,
    _client,
)


def _csrf_headers(client: TestClient) -> dict[str, str]:
    """Issue the same-origin token required by read-only POST advice."""

    response = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    return {
        "origin": ORIGIN,
        "x-csrf-token": response.json()["token"],
    }


def test_advice_returns_only_exact_tool_evidence_in_trusted_scope() -> None:
    """Keep identity and model artifacts out while preserving exact cents."""

    reader = RecordingAdviceReader(
        AdviceRun(
            outcome=ReadOutcome.ANSWERED,
            executed_tool=ReadTool.BUDGETS_AT_RISK,
            facts={
                "period": "2026-09",
                "statuses": [
                    {
                        "category_name": "Spesa alimentare",
                        "target": {"minor_units": 10000, "currency": "EUR"},
                        "posted_spend": {
                            "minor_units": 11250,
                            "currency": "EUR",
                        },
                        "remaining": {"minor_units": -1250, "currency": "EUR"},
                    }
                ],
                "unbudgeted_category_names": ["Abbonamenti"],
            },
            clarification_reason=None,
        )
    )
    client = _client(_app(advice_reader=reader))
    owner_text = "Come stanno andando i budget?"

    response = client.post(
        "/api/v1/advice",
        headers=_csrf_headers(client),
        json={"message": f"  {owner_text}  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "evidence": {
            "kind": "budgets_at_risk",
            "period": {"year": 2026, "month": 9},
            "positions": [
                {
                    "category_name": "Spesa alimentare",
                    "target": {"minor_units": 10000, "currency": "EUR"},
                    "posted_spend": {"minor_units": 11250, "currency": "EUR"},
                    "remaining": {"minor_units": -1250, "currency": "EUR"},
                }
            ],
            "unbudgeted_categories": ["Abbonamenti"],
            "assessment_status": "budget_evidence",
        },
        "reason": None,
    }
    assert reader.requested_profile == PROFILE_ID
    assert reader.requested_message == owner_text
    assert owner_text not in response.text
    assert "profile_id" not in response.text
    assert "prompt" not in response.text
    assert "completion" not in response.text


def test_advice_maps_internal_refusals_to_a_bounded_clarification() -> None:
    """Expose no model completion or internal invalid-output reason."""

    reader = RecordingAdviceReader(
        AdviceRun(
            outcome=ReadOutcome.CLARIFICATION,
            executed_tool=None,
            facts=None,
            clarification_reason=ReadReason.MODEL_OUTPUT_INVALID,
        )
    )
    client = _client(_app(advice_reader=reader))

    response = client.post(
        "/api/v1/advice",
        headers=_csrf_headers(client),
        json={"message": "Dimmi qualcosa"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "needs_clarification",
        "evidence": None,
        "reason": "request_not_understood",
    }
    assert "invalid_model_output" not in response.text


def test_advice_maps_category_spending_and_preserves_signed_refunds() -> None:
    """Expose exact category facts without adding a budget judgment."""

    reader = RecordingAdviceReader(
        AdviceRun(
            outcome=ReadOutcome.ANSWERED,
            executed_tool=ReadTool.CATEGORY_SPENDING,
            facts={
                "period": "2026-09",
                "category_name": "Abbonamenti",
                "posted_spend": {"minor_units": 1799, "currency": "EUR"},
                "split": {
                    "personal_funds": {"minor_units": 2000, "currency": "EUR"},
                    "benefit": {"minor_units": -201, "currency": "EUR"},
                    "other": {"minor_units": 0, "currency": "EUR"},
                },
            },
            clarification_reason=None,
        )
    )
    client = _client(_app(advice_reader=reader))

    response = client.post(
        "/api/v1/advice",
        headers=_csrf_headers(client),
        json={"message": "Quanto ho speso per gli abbonamenti?"},
    )

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence == {
        "kind": "category_spending",
        "period": {"year": 2026, "month": 9},
        "category_name": "Abbonamenti",
        "posted_spend": {"minor_units": 1799, "currency": "EUR"},
        "funding_split": {
            "personal_funds": {"minor_units": 2000, "currency": "EUR"},
            "benefit": {"minor_units": -201, "currency": "EUR"},
            "other": {"minor_units": 0, "currency": "EUR"},
        },
        "assessment_status": "spending_without_threshold",
    }


def test_advice_maps_funding_consumption_without_exposing_wallet_balance() -> None:
    """Keep payment consumption separate from wallet balances."""

    reader = RecordingAdviceReader(
        AdviceRun(
            outcome=ReadOutcome.ANSWERED,
            executed_tool=ReadTool.FUNDING_CLASS_SPLIT,
            facts={
                "period": "2026-09",
                "split": {
                    "personal_funds": {"minor_units": 5000, "currency": "EUR"},
                    "benefit": {"minor_units": 1800, "currency": "EUR"},
                    "other": {"minor_units": 0, "currency": "EUR"},
                },
                "sources": [
                    {
                        "name": "Buoni pasto",
                        "funding_class": "benefit",
                        "posted_spend": {"minor_units": 1800, "currency": "EUR"},
                    }
                ],
            },
            clarification_reason=None,
        )
    )
    client = _client(_app(advice_reader=reader))

    response = client.post(
        "/api/v1/advice",
        headers=_csrf_headers(client),
        json={"message": "Quanto ho usato dei buoni pasto?"},
    )

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence["kind"] == "funding_class_split"
    assert evidence["funding_sources"] == [
        {
            "name": "Buoni pasto",
            "funding_class": "benefit",
            "amount": {"minor_units": 1800, "currency": "EUR"},
        }
    ]
    assert evidence["assessment_status"] == "consumption_not_balance"


def test_advice_is_a_strict_csrf_protected_read_only_post() -> None:
    """Reject browser identity, unsafe cross-site use, and unsupported methods."""

    reader = RecordingAdviceReader(
        AdviceRun(
            outcome=ReadOutcome.ANSWERED,
            executed_tool=ReadTool.BUDGETS_AT_RISK,
            facts={
                "period": "2026-09",
                "statuses": [],
                "unbudgeted_category_names": [],
            },
            clarification_reason=None,
        )
    )
    client = _client(_app(advice_reader=reader))

    without_csrf = client.post("/api/v1/advice", json={"message": "Budget?"})
    headers = _csrf_headers(client)
    injected_profile = client.post(
        "/api/v1/advice",
        headers=headers,
        json={"message": "Budget?", "profile_id": str(PROFILE_ID)},
    )

    assert without_csrf.status_code == 403
    assert injected_profile.status_code == 422
    assert client.get("/api/v1/advice").status_code == 405
    assert reader.requested_message is None


def test_advice_reports_missing_composition_without_falling_back() -> None:
    """Refuse the route when its read-only workflow was not composed."""

    client = _client(_app())
    response = client.post(
        "/api/v1/advice",
        headers=_csrf_headers(client),
        json={"message": "Budget?"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "advice_unavailable"
