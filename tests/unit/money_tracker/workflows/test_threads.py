"""Unit tests for the persistent workflow thread identity contract."""

from __future__ import annotations

import pytest

from money_tracker.workflows import (
    SOURCE_KEYED_NAMESPACES,
    ThreadNamespace,
    workflow_thread_id,
)


def test_namespaces_keep_the_values_already_persisted_in_checkpoints() -> None:
    """Never rename a family: stored thread identifiers carry these strings."""

    assert [namespace.value for namespace in ThreadNamespace] == [
        "router",
        "expense",
        "read",
        "movement-change",
        "category-review",
    ]


def test_only_the_review_family_is_keyed_by_something_other_than_its_source() -> None:
    """Let retention treat every other family as ending with its work unit."""

    assert set(ThreadNamespace) - SOURCE_KEYED_NAMESPACES == {
        ThreadNamespace.CATEGORY_REVIEW
    }


def test_thread_identifier_keeps_a_reference_that_contains_the_separator() -> None:
    """Split only on the first separator, as Telegram references contain more."""

    thread_id = workflow_thread_id(ThreadNamespace.ROUTER, "telegram:test-bot:42")

    assert thread_id == "router:telegram:test-bot:42"
    assert thread_id.split(":", 1) == ["router", "telegram:test-bot:42"]


def test_thread_identifier_requires_a_reference() -> None:
    """Refuse a thread every blank-keyed work unit would share."""

    with pytest.raises(ValueError, match="blank"):
        workflow_thread_id(ThreadNamespace.READ, "  ")
