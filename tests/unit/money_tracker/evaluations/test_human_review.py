"""Tests for the loopback owner usefulness review interface."""

from __future__ import annotations

import http.client
import json
from threading import Thread

import pytest

from money_tracker.evaluations.human_review import (
    HumanReviewItem,
    HumanReviewServer,
    validate_scores,
)


def _request(
    server: HumanReviewServer,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, object] | str, dict[str, str]]:
    """Issue one real HTTP request against the temporary loopback server."""

    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Review-Token"] = token
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read().decode()
    response_headers = dict(response.getheaders())
    connection.close()
    decoded: dict[str, object] | str
    decoded = (
        json.loads(raw)
        if "application/json" in response_headers.get("Content-Type", "")
        else raw
    )
    return response.status, decoded, response_headers


def _items() -> list[HumanReviewItem]:
    """Return two representative structured results for an HTTP session."""

    return [
        HumanReviewItem(
            case_id="ADV-001",
            message="Sto spendendo troppo?",
            observed_outcome="answered",
            observed_answer={
                "period": "2026-09",
                "remaining": {"currency": "EUR", "minor_units": -5000},
            },
        ),
        HumanReviewItem(
            case_id="ADV-002",
            message="Quanto ho speso?",
            observed_outcome="answered",
            observed_answer={
                "total": {"currency": "EUR", "minor_units": 1799},
            },
        ),
    ]


def test_score_validation_requires_the_exact_complete_case_set() -> None:
    """Reject partial, unknown, boolean, and out-of-range submissions."""

    required = {"ADV-001", "ADV-002"}
    assert validate_scores({"ADV-001": 4, "ADV-002": 5}, required) == {
        "ADV-001": 4,
        "ADV-002": 5,
    }
    with pytest.raises(ValueError, match="missing"):
        validate_scores({"ADV-001": 4}, required)
    with pytest.raises(ValueError, match="unknown"):
        validate_scores({"ADV-001": 4, "ADV-002": 5, "ADV-003": 3}, required)
    with pytest.raises(ValueError, match="integers"):
        validate_scores({"ADV-001": True, "ADV-002": 6}, required)


def test_loopback_page_accepts_one_authenticated_complete_submission() -> None:
    """Exercise the real HTTP boundary and its one-shot shutdown behavior."""

    server = HumanReviewServer(_items(), session_id="ADV-test", port=0)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        status, html, headers = _request(server, "GET", "/")
        assert status == 200
        assert isinstance(html, str)
        assert "How useful is this answer?" in html
        assert "Categories without a budget:" in html
        assert "Funding split:" in html
        assert "Sto spendendo troppo?" not in html
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

        status, response, _ = _request(server, "GET", "/api/cases")
        assert status == 200
        assert isinstance(response, dict)
        assert response["session_id"] == "ADV-test"
        assert len(response["cases"]) == 2  # type: ignore[arg-type]

        status, _, _ = _request(
            server,
            "POST",
            "/api/scores",
            payload={"scores": {"ADV-001": 4, "ADV-002": 5}},
            token="wrong",
        )
        assert status == 403
        status, _, _ = _request(
            server,
            "POST",
            "/api/scores",
            payload={"scores": {"ADV-001": 4}},
            token=server.token,
        )
        assert status == 400

        status, response, _ = _request(
            server,
            "POST",
            "/api/scores",
            payload={"scores": {"ADV-001": 4, "ADV-002": 5}},
            token=server.token,
        )
        assert status == 200
        assert response == {"median": 4.5, "saved": 2}
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert server.submitted_scores == {"ADV-001": 4, "ADV-002": 5}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reuse_keeps_only_scores_for_identical_current_results(tmp_path) -> None:
    """Require exact question, outcome, answer, rubric, and owner evidence."""

    from money_tracker.evaluations.human_review import reusable_human_scores

    items = _items()
    path = tmp_path / "human-usefulness-review.json"
    path.write_text(
        json.dumps(
            {
                "rubric_version": "human-usefulness-v1",
                "reviewer_role": "owner",
                "disposition": "completed",
                "scores": [
                    {**items[0].as_json(), "score": 4},
                    {
                        **items[1].as_json(),
                        "observed_answer": {
                            "total": {"currency": "EUR", "minor_units": 1}
                        },
                        "score": 2,
                    },
                ],
            }
        )
    )

    reused, pending = reusable_human_scores(
        path, items, rubric_version="human-usefulness-v1"
    )

    assert reused == {"ADV-001": 4}
    assert [item.case_id for item in pending] == ["ADV-002"]


def test_reuse_rejects_an_unattributable_review(tmp_path) -> None:
    """Do not carry labels across rubrics or from an incomplete review."""

    from money_tracker.evaluations.human_review import reusable_human_scores

    path = tmp_path / "human-usefulness-review.json"
    path.write_text(
        json.dumps(
            {
                "rubric_version": "human-usefulness-v0",
                "reviewer_role": "owner",
                "disposition": "completed",
                "scores": [],
            }
        )
    )

    with pytest.raises(ValueError, match="different rubric"):
        reusable_human_scores(path, _items(), rubric_version="human-usefulness-v1")
