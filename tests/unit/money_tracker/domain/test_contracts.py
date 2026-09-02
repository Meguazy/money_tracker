"""Tests for profile, receipt metadata, and immutable audit contracts."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from money_tracker.domain import AuditEvent, DomainError, Profile, ReceiptMetadata

ROME = ZoneInfo("Europe/Rome")


def test_profile_enforces_the_accepted_v1_locale_and_timezone() -> None:
    """Keep runtime calendar and locale semantics fixed for V1."""

    profile = Profile(uuid4())
    assert profile.locale == "it-IT"
    assert profile.timezone == "Europe/Rome"
    with pytest.raises(DomainError, match="timezone"):
        Profile(uuid4(), timezone="UTC")


def test_receipt_metadata_uses_a_safe_content_address() -> None:
    """Accept a lowercase digest while rejecting paths and unsupported types."""

    metadata = ReceiptMetadata(
        id=uuid4(),
        profile_id=uuid4(),
        content_sha256="a" * 64,
        media_type="image/jpeg",
        byte_size=2048,
        stored_at=datetime(2026, 9, 2, 10, tzinfo=ROME),
        source_reference="api:test",
        original_filename="scontrino.jpg",
    )
    assert metadata.content_sha256 == "a" * 64

    with pytest.raises(DomainError, match="path separators"):
        ReceiptMetadata(
            id=uuid4(),
            profile_id=uuid4(),
            content_sha256="b" * 64,
            media_type="image/png",
            byte_size=10,
            stored_at=datetime(2026, 9, 2, 10, tzinfo=ROME),
            source_reference="api:test",
            original_filename="../private.png",
        )


def test_audit_versions_are_chronological() -> None:
    """Require a mutation event to advance its aggregate version."""

    with pytest.raises(DomainError, match="follow prior"):
        AuditEvent(
            id=uuid4(),
            profile_id=uuid4(),
            aggregate_type="expense",
            aggregate_id=uuid4(),
            action="correct",
            actor="owner",
            source_reference="api:test",
            occurred_at=datetime(2026, 9, 2, 10, tzinfo=ROME),
            prior_version=2,
            current_version=2,
            reason="test",
        )


@pytest.mark.parametrize(
    "digest,media_type,size",
    [
        ("A" * 64, "image/jpeg", 1),
        ("a" * 64, "application/pdf", 1),
        ("a" * 64, "image/png", 0),
    ],
)
def test_receipt_metadata_rejects_unsafe_content_contracts(
    digest: str,
    media_type: str,
    size: int,
) -> None:
    """Fail closed on noncanonical hashes, unsupported formats, and empty media."""

    with pytest.raises(DomainError):
        ReceiptMetadata(
            id=uuid4(),
            profile_id=uuid4(),
            content_sha256=digest,
            media_type=media_type,
            byte_size=size,
            stored_at=datetime(2026, 9, 2, 10, tzinfo=ROME),
            source_reference="api:test",
        )


def test_audit_requires_nonempty_reason_and_aware_time() -> None:
    """Keep operational history useful without accepting ambiguous timestamps."""

    common = {
        "id": uuid4(),
        "profile_id": uuid4(),
        "aggregate_type": "expense",
        "aggregate_id": uuid4(),
        "action": "create",
        "actor": "owner",
        "source_reference": "api:test",
        "prior_version": None,
        "current_version": 1,
    }
    with pytest.raises(DomainError, match="text fields"):
        AuditEvent(
            **common,
            occurred_at=datetime(2026, 9, 2, 10, tzinfo=ROME),
            reason="",
        )
    with pytest.raises(DomainError, match="timezone"):
        AuditEvent(
            **common,
            occurred_at=datetime(2026, 9, 2, 10),
            reason="test",
        )
