"""Deterministic resolution of untrusted extraction values into domain values.

The extraction boundary deliberately keeps two decisions away from the model.

Occurrence time is resolved here, not by the model. The product baseline
anchors relative Italian dates to the source-message timestamp in
`Europe/Rome`, and that arithmetic must be exactly reproducible across a
retry, a resume, and an evaluation run. A model that emitted an ISO instant
would also be performing timezone and daylight-saving reasoning that no gate
could distinguish from a lucky guess.

Amount sign is resolved here for the same reason: the model states a direction
as a word and a positive magnitude, so a stray minus sign cannot silently turn
a purchase into a reimbursement.

Everything in this module is pure. It reads no repository, calls no model, and
holds no state.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from money_tracker.domain import Money
from money_tracker.workflows.contracts import ExpenseExtraction, ExpensePolarity
from money_tracker.workflows.errors import (
    ResolutionReason,
    UnresolvedExtractionError,
)

PRODUCT_TIMEZONE = ZoneInfo("Europe/Rome")
"""Calendar the product baseline uses for occurrence dates and budgets."""


_SAME_DAY_WORDS = frozenset(
    {
        "oggi",
        "stamattina",
        "stamane",
        "stasera",
        "stanotte",
        "questa mattina",
        "questa sera",
        "questo pomeriggio",
        "nel pomeriggio",
        "adesso",
        "ora",
        "poco fa",
        "appena",
        "appena adesso",
        "in questo momento",
    }
)

_PREVIOUS_DAY_WORDS = frozenset(
    {
        "ieri",
        "ieri mattina",
        "ieri matina",
        "ieri pomeriggio",
        "ieri sera",
        "ieri notte",
        "la sera di ieri",
    }
)

_TWO_DAYS_AGO_WORDS = frozenset(
    {
        "l altro ieri",
        "altro ieri",
        "l altroieri",
        "altroieri",
        "avantieri",
        "avant ieri",
    }
)

_NUMBER_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "due": 2,
    "tre": 3,
    "quattro": 4,
    "cinque": 5,
    "sei": 6,
    "sette": 7,
    "otto": 8,
    "nove": 9,
    "dieci": 10,
}

_MONTH_NAMES = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

_WEEKDAY_NAMES = {
    "lunedi": 0,
    "martedi": 1,
    "mercoledi": 2,
    "giovedi": 3,
    "venerdi": 4,
    "sabato": 5,
    "domenica": 6,
}

_DAYS_AGO = re.compile(r"^(?:circa\s+)?(\d{1,3}|[a-z]+)\s+giorn[oi]\s+fa$")
_WEEKS_AGO = re.compile(r"^(?:circa\s+)?(\d{1,3}|[a-z]+)\s+settiman[ae]\s+fa$")
_LAST_WEEK = re.compile(r"^(?:la\s+)?settimana\s+(?:scorsa|passata)$")
_LAST_WEEKDAY = re.compile(
    r"^(?:(?:lo\s+|il\s+|la\s+)?scorso\s+([a-z]+)"
    r"|([a-z]+)\s+(?:scorso|scorsa|passato|passata))$"
)
_NAMED_DAY_MONTH = re.compile(r"^(?:il\s+|l\s+)?(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?$")
_NUMERIC_DATE = re.compile(
    r"^(?:il\s+)?(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?$"
)


def normalize_owner_text(value: str) -> str:
    """Fold owner-supplied text for accent- and case-insensitive matching.

    Owner text arrives with inconsistent accents and spacing, and the
    evaluation datasets include typos, so every match against a known Italian
    form or a configured name happens on a folded form. Apostrophes become
    spaces so that `l'altro ieri` and `l altro ieri` match the same pattern.

    Args:
        value: Verbatim words from the owner message.

    Returns:
        Lowercase, accent-free text with collapsed separators.
    """

    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    folded = without_marks.casefold().replace("'", " ").replace("’", " ")
    return " ".join(folded.split())


def _count(token: str) -> int | None:
    """Read an Italian cardinal written as digits or as a word."""

    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def _rome_instant(target: date, wall_clock: time) -> datetime:
    """Build one canonical aware instant on a Rome calendar day.

    The wall-clock time of the source message is preserved so that a day named
    in prose stays inside the intended Rome day rather than drifting across a
    midnight boundary. A wall time that falls inside the spring-forward gap
    does not exist locally; normalizing through UTC maps it to the equivalent
    instant just after the transition. An ambiguous autumn wall time resolves
    to its first, summer-offset occurrence.

    Args:
        target: Rome calendar day the movement occurred on.
        wall_clock: Rome wall-clock time carried over from the message.

    Returns:
        An aware instant in the product timezone.
    """

    candidate = datetime.combine(target, wall_clock, tzinfo=PRODUCT_TIMEZONE)
    return candidate.astimezone(UTC).astimezone(PRODUCT_TIMEZONE)


def _resolve_offset(text: str) -> int | None:
    """Return a whole-day offset into the past, or None when unrecognized."""

    if text in _SAME_DAY_WORDS:
        return 0
    if text in _PREVIOUS_DAY_WORDS:
        return 1
    if text in _TWO_DAYS_AGO_WORDS:
        return 2
    if _LAST_WEEK.match(text):
        return 7
    days = _DAYS_AGO.match(text)
    if days is not None:
        value = _count(days.group(1))
        return value if value is not None else None
    weeks = _WEEKS_AGO.match(text)
    if weeks is not None:
        value = _count(weeks.group(1))
        return value * 7 if value is not None else None
    return None


def _resolve_last_weekday(text: str, today: date) -> date | None:
    """Resolve `lunedi scorso` to the most recent past day with that weekday.

    A bare weekday name is deliberately not accepted. In Italian it can mean
    either the previous or the coming occurrence, and guessing would silently
    misdate a movement.
    """

    match = _LAST_WEEKDAY.match(text)
    if match is None:
        return None
    name = match.group(1) or match.group(2)
    weekday = _WEEKDAY_NAMES.get(name)
    if weekday is None:
        return None
    delta = (today.weekday() - weekday) % 7
    return today - timedelta(days=delta or 7)


def _resolve_calendar_date(text: str, today: date) -> date | None:
    """Resolve an explicit Italian calendar date, inferring an omitted year.

    A movement has already happened, so an omitted year selects the most recent
    non-future occurrence of that day and month.
    """

    named = _NAMED_DAY_MONTH.match(text)
    if named is not None:
        month = _MONTH_NAMES.get(named.group(2))
        if month is None:
            return None
        return _calendar_date(
            int(named.group(1)),
            month,
            int(named.group(3)) if named.group(3) else None,
            today,
        )
    numeric = _NUMERIC_DATE.match(text)
    if numeric is not None:
        year_token = numeric.group(3)
        year = None
        if year_token is not None:
            year = int(year_token)
            if len(year_token) == 2:
                year += 2000
        return _calendar_date(int(numeric.group(1)), int(numeric.group(2)), year, today)
    return None


def _calendar_date(
    day: int,
    month: int,
    year: int | None,
    today: date,
) -> date | None:
    """Build a real calendar date, choosing the latest non-future year."""

    if year is not None:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for candidate_year in (today.year, today.year - 1):
        try:
            candidate = date(candidate_year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            return candidate
    return None


def resolve_occurrence_time(
    expression: str | None,
    *,
    received_at: datetime,
) -> datetime:
    """Resolve extracted temporal words against the message timestamp.

    Absent words are not an unknown: the product baseline treats a message
    without temporal reference as describing a movement that just happened, so
    the message timestamp is used. Words that are present but unreadable are an
    error, because substituting the message timestamp would silently misdate a
    movement the owner explicitly placed in the past.

    Args:
        expression: Verbatim temporal words, or None when the message stated
            no time.
        received_at: Aware timestamp the channel assigned to the message.

    Returns:
        An aware occurrence instant in the product timezone.

    Raises:
        ValueError: If the message timestamp is naive.
        UnresolvedExtractionError: If stated words cannot be resolved, or
            resolve to a day after the message was sent.
    """

    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("message timestamp must be time-zone aware")
    local = received_at.astimezone(PRODUCT_TIMEZONE)
    if expression is None:
        return local
    text = normalize_owner_text(expression)
    if not text:
        raise UnresolvedExtractionError(ResolutionReason.OCCURRENCE_UNREADABLE)

    today = local.date()
    offset = _resolve_offset(text)
    target: date | None
    if offset is not None:
        target = today - timedelta(days=offset)
    else:
        target = _resolve_last_weekday(text, today) or _resolve_calendar_date(
            text, today
        )
    if target is None:
        raise UnresolvedExtractionError(ResolutionReason.OCCURRENCE_UNREADABLE)
    if target > today:
        raise UnresolvedExtractionError(ResolutionReason.OCCURRENCE_IN_FUTURE)
    return _rome_instant(target, local.time())


def resolve_amount(extraction: ExpenseExtraction) -> Money:
    """Apply the domain sign convention to an extracted magnitude.

    The domain represents a refund as a negative total, so the sign comes from
    the stated polarity rather than from the model's arithmetic.

    Args:
        extraction: Schema-valid extraction.

    Returns:
        Exact signed money in the operational currency.

    Raises:
        UnresolvedExtractionError: If the message stated no amount the model
            could extract.
    """

    if extraction.amount_minor_units is None:
        raise UnresolvedExtractionError(ResolutionReason.AMOUNT_UNKNOWN)
    magnitude = extraction.amount_minor_units
    if extraction.polarity is ExpensePolarity.REFUND:
        return Money(-magnitude)
    return Money(magnitude)
