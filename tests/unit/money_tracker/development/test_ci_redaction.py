"""Unit tests for generated CI evidence redaction."""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

import pytest

from money_tracker.development.ci_redaction import (
    REDACTION_MARKER,
    environment_values,
    main,
    redact_generated_values,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_environment_values_selects_credentials_and_generated_runs() -> None:
    """Keep hosts and names readable while selecting credential-shaped values."""

    dotenv = (
        "# comment\n"
        "FLAG=false\n"
        "LLM_API_KEY=local\n"
        "DATABASE_NAME=money_tracker\n"
        "SERVICE_PASSWORD=not-hex-but-named\n"
        'export QUOTED_SECRET="quoted-secret-value"\n'
        "DATABASE_URL=postgresql://user:0123456789abcdef0123@127.0.0.1/test\n"
        "EMPTY=\n"
    )

    assert set(environment_values(dotenv)) == {
        "not-hex-but-named",
        "quoted-secret-value",
        "postgresql://user:0123456789abcdef0123@127.0.0.1/test",
        "0123456789abcdef0123",
    }


def test_redaction_covers_secret_and_embedded_database_url() -> None:
    """Remove both a generated value and the same password inside a URL."""

    # Generated like `openssl rand -hex`, so no credential-shaped literal is
    # committed for the secret scanner to report.
    password = secrets.token_hex(12)
    dotenv = (
        f"PASSWORD={password}\n"
        f"DATABASE_URL=postgresql://user:{password}@localhost/money_tracker_test\n"
    )
    evidence = (
        f"connection postgresql://user:{password}@localhost/money_tracker_test "
        f"used {password} in tests/unit/money_tracker"
    )

    redacted = redact_generated_values(evidence, dotenv)

    assert password not in redacted
    assert redacted.count(REDACTION_MARKER) == 2
    assert redacted.endswith("tests/unit/money_tracker")


def test_redaction_also_removes_token_shaped_runs() -> None:
    """Apply the shared Bot API token redaction as a second defence."""

    redacted = redact_generated_values("GET /bot123456:" + "A" * 30, "")

    assert "A" * 30 not in redacted


def test_command_redacts_the_generator_output_into_a_new_file(tmp_path: Path) -> None:
    """Redact every generated credential produced by the real CI generator."""

    env_file = tmp_path / "generated.env"
    subprocess.run(
        [str(_REPOSITORY_ROOT / "scripts/generate_local_env.sh")],
        check=True,
        capture_output=True,
        env={
            "MONEY_TRACKER_ENV_FILE": str(env_file),
            "HOME": str(tmp_path),
            "PATH": os.environ["PATH"],
        },
    )
    dotenv = env_file.read_text(encoding="utf-8")
    secrets = [
        line.split("=", 1)[1]
        for line in dotenv.splitlines()
        if line.startswith(("MONEY_TRACKER_POSTGRES_PASSWORD=", "LANGFUSE_SECRET_KEY="))
    ]
    evidence = tmp_path / "raw" / "compose.log"
    evidence.parent.mkdir()
    evidence.write_text(dotenv, encoding="utf-8")
    output = tmp_path / "sanitized" / "compose.log"

    main(
        ["--env-file", str(env_file), "--input", str(evidence), "--output", str(output)]
    )

    retained = output.read_text(encoding="utf-8")
    assert len(secrets) == 2
    assert all(secret not in retained for secret in secrets)
    assert "MONEY_TRACKER_BACKUP_EXPECTED_DATABASE=money_tracker\n" in retained


def test_command_fails_without_writing_when_evidence_is_missing(
    tmp_path: Path,
) -> None:
    """Exit non-zero and leave no partial output for an absent input."""

    env_file = tmp_path / "generated.env"
    env_file.write_text(f"PASSWORD={secrets.token_hex(8)}\n", encoding="utf-8")
    output = tmp_path / "sanitized" / "missing.log"

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--env-file",
                str(env_file),
                "--input",
                str(tmp_path / "missing.log"),
                "--output",
                str(output),
            ]
        )

    assert raised.value.code == 1
    assert not output.exists()
