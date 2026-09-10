"""Real-process integration tests for safe Telegram runtime failures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres


def _environment(postgres_url: str) -> dict[str, str]:
    """Build non-secret process configuration against the isolated database."""

    environment = os.environ.copy()
    environment.update(
        {
            "MONEY_TRACKER_DATABASE_URL": postgres_url,
            "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": "test",
            "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE": (
                "/tmp/money-tracker-unused-production-token"
            ),
            "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE": (
                "/tmp/money-tracker-unused-test-token"
            ),
        }
    )
    return environment


@pytest.mark.parametrize(
    ("entrypoint", "command", "prefix"),
    [
        (
            ".venv/bin/money-tracker-telegram",
            "health",
            "Telegram gateway failed",
        ),
        (
            ".venv/bin/money-tracker-worker",
            "process-once",
            "Workflow worker failed",
        ),
    ],
)
def test_unmigrated_schema_stops_safely_without_a_traceback(
    postgres_url: str, entrypoint: str, command: str, prefix: str
) -> None:
    """Classify an empty real database as an operator-fixable schema error."""

    completed = subprocess.run(
        [str(Path(entrypoint).resolve()), command],
        check=False,
        capture_output=True,
        env=_environment(postgres_url),
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == f"{prefix}: database_schema_invalid\n"
    assert "Traceback" not in completed.stderr
