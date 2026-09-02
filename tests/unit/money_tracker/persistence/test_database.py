"""Unit tests for application PostgreSQL settings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from money_tracker.persistence.database import (
    DatabaseSettings,
    _checkpoint_serializer_from_env,
)


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
