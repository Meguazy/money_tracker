"""Tests for the last-line removal of forbidden values from trace payloads."""

from __future__ import annotations

from pathlib import Path

import pytest

from money_tracker.tracing.redaction import TraceRedactor


def test_owner_content_is_kept_verbatim() -> None:
    """Full fidelity: an ordinary message, figure, or category passes unchanged."""

    payload = {
        "text": "Ho speso 12,50 euro al supermercato con i buoni pasto",
        "facts": {"posted_spend": {"minor_units": 1250, "currency": "EUR"}},
        "names": ["Cibo e accessori", "Spesa"],
    }

    assert TraceRedactor().redact(payload) == payload


def test_bot_tokens_and_telegram_urls_are_removed() -> None:
    """Remove a Bot API credential wherever it hides."""

    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    redacted = TraceRedactor().redact(
        {
            "text": f"il token è {token}",
            "url": f"https://api.telegram.org/bot{token}/getMe",
        }
    )

    assert token not in str(redacted)
    assert "api.telegram.org" not in str(redacted)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://money_tracker:hunter2hunter2@127.0.0.1:5433/money_tracker",
        "postgres://user@localhost/db",
        "postgresql+psycopg://a:b@host/db",
        "https://someone:password@example.org/path",
    ],
)
def test_database_and_credential_urls_are_removed(url: str) -> None:
    """Remove every database URL and every URL carrying user information."""

    redacted = TraceRedactor().redact(f"errore: {url} irraggiungibile")

    assert url not in redacted
    assert "irraggiungibile" in redacted


def test_configured_secret_literals_are_removed_but_short_values_are_not() -> None:
    """Replace a configured secret, and leave a short default alone."""

    redactor = TraceRedactor(secrets=["sk-lf-0123456789abcdef", "local"])

    redacted = redactor.redact(
        {"a": "chiave sk-lf-0123456789abcdef", "b": "local-model risponde"}
    )

    assert redacted == {"a": "chiave <redacted-secret>", "b": "local-model risponde"}


def test_secrets_are_removed_from_keys_and_nested_sequences() -> None:
    """Walk mappings, lists, and tuples, keys included."""

    redactor = TraceRedactor(secrets=["sk-lf-0123456789abcdef"])

    redacted = redactor.redact(
        {"sk-lf-0123456789abcdef": [("x", "sk-lf-0123456789abcdef")]}
    )

    assert "sk-lf-0123456789abcdef" not in str(redacted)


def test_receipt_archive_paths_are_removed(tmp_path: Path) -> None:
    """Remove the archive root and anything beneath it."""

    root = tmp_path / "receipts"
    redactor = TraceRedactor(private_roots=[str(root)])

    redacted = redactor.redact(f"letto {root}/ab/cd/abcdef.jpg ok")

    assert str(root) not in redacted
    assert redacted == "letto <private-path> ok"


def test_raw_bytes_never_enter_a_trace() -> None:
    """Replace receipt bytes with their length."""

    redacted = TraceRedactor().redact({"image": b"\x89PNG\r\n", "view": bytearray(3)})

    assert redacted == {"image": "<bytes length=6>", "view": "<bytes length=3>"}


def test_environment_secrets_and_roots_are_learned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Know this process's configured credentials and archive root."""

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-environment-secret")
    monkeypatch.setenv(
        "MONEY_TRACKER_DATABASE_URL",
        "postgresql://money_tracker:database-password-value@127.0.0.1:5433/db",
    )
    monkeypatch.setenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", str(tmp_path))
    redactor = TraceRedactor.from_env()

    redacted = redactor(
        data="sk-lf-environment-secret database-password-value " + str(tmp_path)
    )

    assert redacted == "<redacted-secret> <redacted-secret> <private-path>"
