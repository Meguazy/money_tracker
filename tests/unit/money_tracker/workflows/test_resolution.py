"""Tests for deterministic resolution of extracted values into domain values."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from money_tracker.domain import Money
from money_tracker.workflows import (
    PRODUCT_TIMEZONE,
    ExpenseExtraction,
    ResolutionReason,
    UnresolvedExtractionError,
    resolve_amount,
    resolve_occurrence_for,
    resolve_occurrence_time,
)

# A Wednesday evening in Rome summer time (UTC+2).
RECEIVED_AT = datetime(2026, 9, 2, 21, 30, tzinfo=PRODUCT_TIMEZONE)


def _extraction(**overrides: Any) -> ExpenseExtraction:
    """Build one schema-valid extraction for resolution tests."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "currency": "EUR",
        "description": "croccantini",
        "evidence": "12 euro di croccantini",
    }
    payload.update(overrides)
    return ExpenseExtraction.model_validate_json(json.dumps(payload))


def _resolved_date(expression: str | None) -> tuple[int, int, int]:
    """Resolve one expression and return its Rome calendar day."""

    resolved = resolve_occurrence_time(expression, received_at=RECEIVED_AT)
    local = resolved.astimezone(PRODUCT_TIMEZONE)
    return (local.year, local.month, local.day)


def test_absent_temporal_words_use_the_message_timestamp() -> None:
    """Treat silence as a movement that just happened, not as a question."""

    resolved = resolve_occurrence_time(None, received_at=RECEIVED_AT)

    assert resolved == RECEIVED_AT
    assert resolved.utcoffset() is not None


def test_resolution_requires_an_aware_message_timestamp() -> None:
    """Refuse to anchor a relative date to an ambiguous instant."""

    with pytest.raises(ValueError, match="time-zone aware"):
        resolve_occurrence_time("ieri", received_at=datetime(2026, 9, 2, 21, 30))


def test_resolution_normalizes_a_utc_message_timestamp_to_rome() -> None:
    """Bucket a movement by the Rome day the owner experienced."""

    # 23:30 UTC is already the next day in Rome summer time.
    late = datetime(2026, 9, 2, 23, 30, tzinfo=UTC)

    resolved = resolve_occurrence_time(None, received_at=late)

    assert resolved.astimezone(PRODUCT_TIMEZONE).day == 3


@pytest.mark.parametrize(
    "expression",
    ["oggi", "stamattina", "stasera", "adesso", "poco fa", "appena", "Oggi"],
)
def test_same_day_words_resolve_to_the_message_day(expression: str) -> None:
    """Accept the common Italian ways of naming today."""

    assert _resolved_date(expression) == (2026, 9, 2)


@pytest.mark.parametrize(
    "expression",
    ["ieri", "ieri sera", "ieri mattina", "IERI", "  ieri  "],
)
def test_previous_day_words_resolve_to_yesterday(expression: str) -> None:
    """Accept yesterday with or without a time of day."""

    assert _resolved_date(expression) == (2026, 9, 1)


@pytest.mark.parametrize(
    "expression",
    ["l'altro ieri", "l altro ieri", "altroieri", "avantieri", "avant'ieri"],
)
def test_two_days_ago_words_handle_apostrophes(expression: str) -> None:
    """Fold apostrophe variants onto the same offset."""

    assert _resolved_date(expression) == (2026, 8, 31)


@pytest.mark.parametrize(
    ("expression", "expected_day"),
    [
        ("3 giorni fa", 30),
        ("tre giorni fa", 30),
        ("un giorno fa", 1),
        ("circa 2 giorni fa", 31),
    ],
)
def test_counted_day_offsets_accept_digits_and_words(
    expression: str,
    expected_day: int,
) -> None:
    """Read an Italian cardinal written either way."""

    assert _resolved_date(expression)[2] == expected_day


@pytest.mark.parametrize(
    "expression",
    ["una settimana fa", "la settimana scorsa", "settimana passata", "1 settimana fa"],
)
def test_week_offsets_resolve_to_seven_days_earlier(expression: str) -> None:
    """Treat a named past week as a whole-week offset."""

    assert _resolved_date(expression) == (2026, 8, 26)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # 2 September 2026 is a Wednesday.
        ("lunedì scorso", (2026, 8, 31)),
        ("lunedi scorso", (2026, 8, 31)),
        ("scorso lunedì", (2026, 8, 31)),
        ("martedì passato", (2026, 9, 1)),
        ("venerdì scorso", (2026, 8, 28)),
        ("mercoledì scorso", (2026, 8, 26)),
    ],
)
def test_named_past_weekday_resolves_to_the_most_recent_occurrence(
    expression: str,
    expected: tuple[int, int, int],
) -> None:
    """Resolve a named weekday strictly into the past.

    The same weekday as the message means the previous week, never today.
    """

    assert _resolved_date(expression) == expected


@pytest.mark.parametrize("expression", ["lunedì", "martedi", "domenica"])
def test_bare_weekday_is_not_resolved(expression: str) -> None:
    """Refuse a weekday that could mean the past or the coming occurrence."""

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_occurrence_time(expression, received_at=RECEIVED_AT)

    assert error.value.reason is ResolutionReason.OCCURRENCE_UNREADABLE


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("il 3 marzo", (2026, 3, 3)),
        ("3 marzo", (2026, 3, 3)),
        ("il 3 marzo 2025", (2025, 3, 3)),
        ("1/9", (2026, 9, 1)),
        ("01/09/2026", (2026, 9, 1)),
        ("1-9-26", (2026, 9, 1)),
        ("31/12", (2025, 12, 31)),
        ("il 25 dicembre", (2025, 12, 25)),
    ],
)
def test_explicit_dates_use_italian_order_and_infer_a_past_year(
    expression: str,
    expected: tuple[int, int, int],
) -> None:
    """Read day-before-month and pick the most recent non-future year."""

    assert _resolved_date(expression) == expected


@pytest.mark.parametrize(
    "expression",
    ["il 3 marzo 2027", "03/09/2026", "3/9/2026", "il 3 settembre 2026"],
)
def test_a_future_day_is_refused_rather_than_recorded(expression: str) -> None:
    """Keep a movement in the past instead of silently misdating it.

    An explicitly stated future year is refused outright. A day and month
    without a year never resolves forward, because an omitted year selects the
    most recent non-future occurrence instead.
    """

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_occurrence_time(expression, received_at=RECEIVED_AT)

    assert error.value.reason is ResolutionReason.OCCURRENCE_IN_FUTURE


@pytest.mark.parametrize(
    "expression",
    ["l'altro giorno", "qualche tempo fa", "prima", "30 febbraio", "99/99", ""],
)
def test_unreadable_words_are_an_error_not_a_default(expression: str) -> None:
    """Never substitute the message timestamp for words the owner wrote."""

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_occurrence_time(expression, received_at=RECEIVED_AT)

    assert error.value.reason is ResolutionReason.OCCURRENCE_UNREADABLE


def test_resolution_preserves_the_message_time_of_day() -> None:
    """Keep a named day inside its intended Rome day."""

    # 00:30 Rome time: a naive UTC-based offset would land on the wrong day.
    just_after_midnight = datetime(2026, 9, 2, 0, 30, tzinfo=PRODUCT_TIMEZONE)

    resolved = resolve_occurrence_time("ieri", received_at=just_after_midnight)
    local = resolved.astimezone(PRODUCT_TIMEZONE)

    assert (local.year, local.month, local.day) == (2026, 9, 1)
    assert (local.hour, local.minute) == (0, 30)


def test_resolution_crosses_a_daylight_saving_transition() -> None:
    """Return a correct instant when the offset changes between the days."""

    # 29 March 2026 is the Rome spring-forward day: 02:00 becomes 03:00.
    after_transition = datetime(2026, 3, 29, 12, 0, tzinfo=PRODUCT_TIMEZONE)

    resolved = resolve_occurrence_time("ieri", received_at=after_transition)
    local = resolved.astimezone(PRODUCT_TIMEZONE)

    assert (local.month, local.day, local.hour) == (3, 28, 12)
    assert after_transition.utcoffset() != local.utcoffset()


def test_a_wall_time_inside_the_spring_forward_gap_stays_a_real_instant() -> None:
    """Map a nonexistent local time onto the instant after the transition."""

    # 02:30 does not exist in Rome on 29 March 2026.
    next_day = datetime(2026, 3, 30, 2, 30, tzinfo=PRODUCT_TIMEZONE)

    resolved = resolve_occurrence_time("ieri", received_at=next_day)
    local = resolved.astimezone(PRODUCT_TIMEZONE)

    assert (local.month, local.day) == (3, 29)
    assert local.utcoffset() is not None
    # The instant round-trips, which a nonexistent wall time would not.
    assert local.astimezone(UTC).astimezone(PRODUCT_TIMEZONE) == local


def test_expense_amount_keeps_the_stated_magnitude_positive() -> None:
    """Apply the domain sign convention for spending."""

    assert resolve_amount(_extraction()) == Money(1200)


def test_refund_amount_becomes_negative_from_polarity_alone() -> None:
    """Derive a reimbursement's sign from the word, not the model's math."""

    assert resolve_amount(_extraction(polarity="refund")) == Money(-1200)


def test_unknown_amount_cannot_be_resolved() -> None:
    """Ask instead of inventing a total."""

    extraction = _extraction(
        amount_minor_units=None, currency=None, unknowns=["amount"]
    )

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_amount(extraction)

    assert error.value.reason is ResolutionReason.AMOUNT_UNKNOWN


def test_extraction_occurrence_resolves_through_its_declared_words() -> None:
    """Resolve the extraction's own temporal words against the message."""

    resolved = resolve_occurrence_for(
        _extraction(occurred_at_text="ieri"), received_at=RECEIVED_AT
    )

    assert resolved.astimezone(PRODUCT_TIMEZONE).day == 1


def test_extraction_without_temporal_words_uses_the_message_timestamp() -> None:
    """Apply the documented default for a movement that just happened."""

    resolved = resolve_occurrence_for(_extraction(), received_at=RECEIVED_AT)

    assert resolved == RECEIVED_AT


def test_declared_unknown_occurrence_is_not_defaulted() -> None:
    """Keep an explicit abstention distinct from silence."""

    extraction = _extraction(unknowns=["occurred_at"])

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_occurrence_for(extraction, received_at=RECEIVED_AT)

    assert error.value.reason is ResolutionReason.OCCURRENCE_UNKNOWN


@pytest.mark.parametrize(
    "expression",
    [
        "scorso pippo",
        "pippo scorso",
        "il 3 pippo",
        "il 30 febbraio 2025",
        "il 31 aprile 2025",
    ],
)
def test_wellformed_but_meaningless_dates_are_unresolved(expression: str) -> None:
    """Refuse text that fits a date pattern but names no real day.

    A weekday or month word the calendar does not know, and an explicit year
    with an impossible day, are unresolved rather than nudged to a nearby date.
    """

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_occurrence_time(expression, received_at=RECEIVED_AT)

    assert error.value.reason is ResolutionReason.OCCURRENCE_UNREADABLE
