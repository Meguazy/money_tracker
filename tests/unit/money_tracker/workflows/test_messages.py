"""Tests for the normalized channel-agnostic inbound work unit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from money_tracker.domain import DomainError
from money_tracker.workflows import CallbackAction, ChannelMessage

RECEIVED_AT = datetime(2026, 9, 2, 18, 30, tzinfo=UTC)


def test_message_requires_a_source_reference() -> None:
    """Keep every routed message correlatable to its durable inbound record."""

    with pytest.raises(DomainError, match="source reference"):
        ChannelMessage(source_reference="   ", received_at=RECEIVED_AT, text="ciao")


def test_message_requires_an_aware_timestamp() -> None:
    """Anchor relative Italian dates to an unambiguous instant."""

    with pytest.raises(DomainError, match="time-zone aware"):
        ChannelMessage(
            source_reference="channel:inbox:1",
            received_at=datetime(2026, 9, 2, 18, 30),
            text="ieri ho speso 12 euro",
        )


def test_message_accepts_a_non_utc_offset() -> None:
    """Accept the Rome offset the channel actually reports."""

    rome_offset = timezone(timedelta(hours=2))

    message = ChannelMessage(
        source_reference="channel:inbox:1",
        received_at=datetime(2026, 9, 2, 20, 30, tzinfo=rome_offset),
        text="ieri ho speso 12 euro",
    )

    assert message.received_at.utcoffset() == timedelta(hours=2)


def test_message_requires_processable_content() -> None:
    """Refuse an update that carries no text, decision, or media."""

    with pytest.raises(DomainError, match="no processable content"):
        ChannelMessage(source_reference="channel:inbox:1", received_at=RECEIVED_AT)


def test_callback_only_message_normalizes_to_empty_text() -> None:
    """Expose absent text as an empty string rather than None downstream."""

    message = ChannelMessage(
        source_reference="channel:inbox:1",
        received_at=RECEIVED_AT,
        callback_action=CallbackAction.APPROVE_CATEGORY,
        pending_action_id=uuid4(),
    )

    assert message.text is None
    assert message.normalized_text == ""


def test_text_normalization_collapses_whitespace() -> None:
    """Compare owner text without depending on channel spacing."""

    message = ChannelMessage(
        source_reference="channel:inbox:1",
        received_at=RECEIVED_AT,
        text="  ho   speso\n12 euro  ",
    )

    assert message.normalized_text == "ho speso 12 euro"


def test_image_only_message_is_processable() -> None:
    """Accept an uncaptioned photo as a review-only receipt candidate."""

    message = ChannelMessage(
        source_reference="channel:inbox:1",
        received_at=RECEIVED_AT,
        has_image=True,
    )

    assert message.has_image
    assert message.normalized_text == ""
