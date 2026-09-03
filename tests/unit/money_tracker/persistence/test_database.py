"""Unit tests for application PostgreSQL settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from money_tracker.persistence.database import (
    CHECKPOINT_ALLOWED_TYPES,
    DatabaseSettings,
    _checkpoint_serializer_from_env,
)
from money_tracker.workflows import CallbackAction, ReviewDecision, ReviewQuestion


@dataclass(frozen=True)
class _Unregistered:
    """A type this repository owns but never allowlists for checkpoints."""

    secret: str


def _round_trip(serializer: JsonPlusSerializer, value: object) -> object:
    """Serialize and restore one value the way a checkpoint would."""

    return serializer.loads_typed(serializer.dumps_typed(value))


def test_database_url_is_required() -> None:
    """Reject an absent application database URL with its variable name."""

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(
            RuntimeError, match="MONEY_TRACKER_DATABASE_URL is required"
        ):
            DatabaseSettings.from_env()


def test_database_url_must_use_postgresql() -> None:
    """Reject a non-PostgreSQL persistence boundary."""

    with patch.dict(
        os.environ,
        {"MONEY_TRACKER_DATABASE_URL": "sqlite:///money-tracker.db"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="must use PostgreSQL"):
            DatabaseSettings.from_env()


def test_database_secret_is_omitted_from_repr() -> None:
    """Keep connection credentials out of diagnostics and assertion output."""

    database_url = "postgresql://money_tracker:private-value@127.0.0.1/db"
    with patch.dict(
        os.environ,
        {"MONEY_TRACKER_DATABASE_URL": database_url},
        clear=True,
    ):
        settings = DatabaseSettings.from_env()

    assert settings.database_url == database_url
    assert "private-value" not in repr(settings)


def test_insecure_checkpoint_serialization_is_rejected() -> None:
    """Refuse configuration that could deserialize pickle database payloads."""

    with patch.dict(os.environ, {"LANGGRAPH_STRICT_MSGPACK": "false"}, clear=True):
        with pytest.raises(RuntimeError, match="pickle fallback is prohibited"):
            _checkpoint_serializer_from_env()


def test_strictness_does_not_depend_on_an_absent_environment_variable() -> None:
    """Keep the invariant when nothing sets the library's own switch.

    The library resolves `LANGGRAPH_STRICT_MSGPACK` once at import time and
    defaults it to false, while this repository treats strictness as the
    default. Passing the allowlist explicitly is what closes that gap, so an
    unset variable can no longer decide whether unregistered types are
    reconstructed from a checkpoint.
    """

    with patch.dict(os.environ, {}, clear=True):
        serializer = _checkpoint_serializer_from_env()

    restored = _round_trip(serializer, _Unregistered(secret="value"))

    assert serializer.pickle_fallback is False
    assert not isinstance(restored, _Unregistered)
    assert restored == {"secret": "value"}


def test_only_the_contracts_that_cross_a_pause_are_reconstructable() -> None:
    """Allow this repository's pause contracts and nothing else of its own.

    A paused review outlives its process, so the question the owner is shown
    and the decision that resumes it must survive as themselves. The list stays
    minimal because every entry is a type a checkpoint may instantiate.
    """

    allowed = {name for _, name in CHECKPOINT_ALLOWED_TYPES}
    modules = {module for module, _ in CHECKPOINT_ALLOWED_TYPES}

    assert allowed == {"ReviewQuestion", "ReviewDecision", "CallbackAction"}
    assert all(module.startswith("money_tracker.workflows") for module in modules)


@pytest.mark.parametrize(
    "value",
    [
        ReviewQuestion(
            proposal_id="11111111-1111-4111-8111-111111111111",
            candidate_name="Giardinaggio",
            parent_name=None,
            rationale="Nessuna categoria attiva descrive lo scopo.",
            alternative_names=("Cibo e accessori", "Veterinario"),
            can_repropose=True,
        ),
        ReviewDecision(CallbackAction.APPROVE_CATEGORY),
        ReviewDecision(CallbackAction.USE_NAMED_EXISTING, "Spesa alimentare"),
        CallbackAction.CANCEL_REVIEW,
    ],
)
def test_a_paused_review_survives_a_restart_as_itself(value: object) -> None:
    """Restore a pause payload as its contract, not as an anonymous mapping.

    A persistent checkpointer exists so a review can outlive the process that
    opened it, which is worth nothing if the owner cannot then be shown the
    same question. Equality is asserted rather than field access because a
    sequence returning as a list would restore an unhashable, unequal value
    while every field still read correctly.
    """

    serializer = _checkpoint_serializer_from_env()

    restored = _round_trip(serializer, value)

    assert type(restored) is type(value)
    assert restored == value
    assert hash(restored) == hash(value)


def test_isolated_test_database_has_a_distinct_variable() -> None:
    """Select the test URL only when the caller explicitly requests it."""

    environment = {
        "MONEY_TRACKER_DATABASE_URL": "postgresql://money_tracker:a@localhost/app",
        "MONEY_TRACKER_TEST_DATABASE_URL": "postgresql://money_tracker:b@localhost/test",
    }
    with patch.dict(os.environ, environment, clear=True):
        production = DatabaseSettings.from_env()
        integration = DatabaseSettings.from_env(test_database=True)

    assert production.database_url.endswith("/app")
    assert integration.database_url.endswith("/test")
