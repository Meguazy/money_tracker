"""Executable probes for local structured output, tools, sequencing, and images."""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue

from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    as_object_mapping,
    collect_environment_metadata,
    collect_model_metadata,
    collect_source_metadata,
    sha256_file,
)
from money_tracker.evaluations.models import (
    CapabilityReport,
    CapabilityRunResult,
    DatasetMetadata,
    ProbeResult,
    ProbeStatus,
)
from money_tracker.evaluations.suites import candidate_catalog, model_key


class _LineItem(BaseModel):
    """Nested item used to exercise constrained list output."""

    model_config = ConfigDict(extra="forbid")

    description: str
    amount_minor: int


class _StructuredExpense(BaseModel):
    """Representative strict schema with enum, nullable field, and nested items."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int
    currency: Literal["EUR"]
    status: Literal["draft", "posted"]
    note: str | None
    items: list[_LineItem]


class _PaymentPart(BaseModel):
    """One payment method the owner named, mirroring a real boundary's shape."""

    model_config = ConfigDict(extra="forbid")

    method: str
    amount_minor: int | None


class _OptionalFieldDraft(BaseModel):
    """Draft whose four optional slots the message usually leaves unstated.

    The width is deliberate. Three product boundaries fill an optional field
    the message never states, and each of them offers the model several
    optional slots at once inside a longer instruction. A probe holding one
    optional field would be easier than the boundary it screens and could
    clear a capability the boundary then fails, so this schema carries the
    three optionality encodings a contract can use: a nullable scalar, a
    nullable string, and an emptiable list.
    """

    model_config = ConfigDict(extra="forbid")

    amount_minor: int | None
    description: str
    occurred_at_text: str | None
    merchant: str | None
    payment_parts: list[_PaymentPart]


class _ExistingCategoryChoice(BaseModel):
    """Union variant selecting a category the owner's list already holds."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["existing"]
    category_name: str


class _ProposedCategoryChoice(BaseModel):
    """Union variant offering a new category for owner approval."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["proposal"]
    proposed_name: str
    rationale: str


ALWAYS_STATED_DRAFT_FIELDS = ("description",)
"""Draft properties every message fills, so absence never expresses silence."""

OPTIONAL_DRAFT_SLOTS = (
    "amount_minor",
    "occurred_at_text",
    "merchant",
    "payment_parts",
)
"""Draft properties a message may leave unstated, scored slot by slot."""

EMPTY_PLACEHOLDERS = frozenset(
    {
        "-",
        "?",
        "n/a",
        "na",
        "nessuna",
        "nessuno",
        "non disponibile",
        "non specificato",
        "none",
        "null",
        "sconosciuta",
        "sconosciuto",
        "unknown",
    }
)
"""Words a model writes instead of leaving a slot empty.

These are counted apart from empty text and apart from invented values,
because the three have different consequences. A contract constraining its
text to a non-empty string refuses empty text loudly and spends a retry, but
it accepts one of these words and stores it as the merchant's name or as a
payment method, which is the silent corruption the extraction instructions
warn against by name.
"""


class _ProbeError(RuntimeError):
    """Expected deterministic failure that contains only safe diagnostics."""


ProbeCallable = Callable[[], dict[str, JsonValue]]


def _case_by_capability(
    cases: list[dict[str, object]], capability: str
) -> dict[str, object]:
    """Return the single dataset case for a stable capability identifier."""

    matches = [case for case in cases if case.get("capability") == capability]
    if len(matches) != 1:
        raise _ProbeError(f"dataset_case_count:{capability}:{len(matches)}")
    return matches[0]


def _load_cases(path: Path) -> list[dict[str, object]]:
    """Load synthetic JSONL cases without accepting non-object records."""

    cases: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise _ProbeError(f"dataset_record_not_object:{line_number}")
        cases.append(decoded)
    return cases


def _dataset_version(cases: list[dict[str, object]]) -> str:
    """Return the single explicit dataset version shared by every case.

    Args:
        cases: Decoded dataset records.

    Returns:
        The version declared consistently by all cases.

    Raises:
        _ProbeError: If cases omit a version or mix multiple versions.
    """

    versions: set[str] = set()
    for case in cases:
        version = case.get("dataset_version")
        if not isinstance(version, str) or not version:
            raise _ProbeError("dataset_version_missing")
        versions.add(version)
    if len(versions) != 1:
        raise _ProbeError("dataset_version_not_unique")
    return next(iter(versions))


def _request(
    client: httpx.Client,
    settings: CapabilitySettings,
    payload: dict[str, object],
) -> tuple[Mapping[str, object], int]:
    """Send one local chat request and return decoded JSON plus latency."""

    started = time.monotonic()
    response = client.post(
        f"{settings.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        json=payload,
    )
    latency_ms = round((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        raise _ProbeError(f"http_status:{response.status_code}")
    try:
        return as_object_mapping(response.json()), latency_ms
    except (ValueError, TypeError) as error:
        raise _ProbeError("response_not_json_object") from error


def _message(response: Mapping[str, object]) -> Mapping[str, object]:
    """Extract the first assistant message from an OpenAI-compatible response."""

    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _ProbeError("missing_choices")
    choice = as_object_mapping(choices[0])
    return as_object_mapping(choice.get("message"))


def _text_content(message: Mapping[str, object]) -> str:
    """Extract plain assistant text without persisting raw response objects."""

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _ProbeError("missing_text_content")
    return content.strip()


def _tool_call(message: Mapping[str, object]) -> Mapping[str, object]:
    """Extract exactly one assistant tool call."""

    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise _ProbeError("expected_one_tool_call")
    return as_object_mapping(tool_calls[0])


def _structured_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Require a nested, strict, extra-forbidden JSON result."""

    prompt = str(case.get("prompt", ""))
    expected = as_object_mapping(case.get("expected"))
    schema = _StructuredExpense.model_json_schema()
    payload: dict[str, object] = {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Return only data that matches the supplied JSON schema.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "money_tracker_capability_expense",
                "strict": True,
                "schema": schema,
            },
        },
        "max_tokens": 512,
    }
    response, latency = _request(client, settings, payload)
    content = _text_content(_message(response))
    try:
        parsed = _StructuredExpense.model_validate_json(content)
    except ValueError as error:
        raise _ProbeError("schema_validation_failed") from error
    if parsed.model_dump(mode="json") != dict(expected):
        raise _ProbeError("structured_value_mismatch")
    return {"latency_ms": latency, "schema": "strict_nested_v1"}


def _tool_definition() -> dict[str, object]:
    """Return the exact read-only tool contract used by capability probes."""

    return {
        "type": "function",
        "function": {
            "name": "get_budget_remaining",
            "description": "Return a deterministic remaining budget for a category.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category_id": {"type": "string", "enum": ["subscriptions"]}
                },
                "required": ["category_id"],
            },
        },
    }


def _tool_request_payload(
    settings: CapabilitySettings,
    prompt: str,
) -> dict[str, object]:
    """Build a request that must select the one permitted read tool."""

    return {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Use the provided tool. Never invent its result.",
            },
            {"role": "user", "content": prompt},
        ],
        "tools": [_tool_definition()],
        "tool_choice": "required",
        "max_tokens": 256,
    }


def _validate_tool_call(call: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    """Validate exact function name and JSON arguments from one tool call."""

    call_id = call.get("id")
    function = as_object_mapping(call.get("function"))
    if not isinstance(call_id, str) or not call_id:
        raise _ProbeError("missing_tool_call_id")
    if function.get("name") != "get_budget_remaining":
        raise _ProbeError("wrong_tool_name")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise _ProbeError("tool_arguments_not_json_string")
    decoded = json.loads(arguments)
    if decoded != {"category_id": "subscriptions"}:
        raise _ProbeError("wrong_tool_arguments")
    return call_id, decoded


def _tool_call_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Require a strict read-only tool call with exact arguments."""

    response, latency = _request(
        client,
        settings,
        _tool_request_payload(settings, str(case.get("prompt", ""))),
    )
    _, arguments = _validate_tool_call(_tool_call(_message(response)))
    return {
        "latency_ms": latency,
        "tool_name": "get_budget_remaining",
        "argument_keys": cast(list[JsonValue], sorted(arguments)),
    }


def _tool_sequence_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Require a model to consume a real tool result before answering."""

    prompt = str(case.get("prompt", ""))
    first_payload = _tool_request_payload(settings, prompt)
    first_response, first_latency = _request(client, settings, first_payload)
    first_message = _message(first_response)
    call = _tool_call(first_message)
    call_id, _ = _validate_tool_call(call)

    assistant_message = {
        "role": "assistant",
        "content": first_message.get("content"),
        "tool_calls": [dict(call)],
    }
    second_payload: dict[str, object] = {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Answer only from tool results and include the EUR amount.",
            },
            {"role": "user", "content": prompt},
            assistant_message,
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "get_budget_remaining",
                "content": json.dumps(
                    {
                        "category_id": "subscriptions",
                        "remaining_minor": 3700,
                        "currency": "EUR",
                        "period": "2026-09",
                    }
                ),
            },
        ],
        "tools": [_tool_definition()],
        "tool_choice": "none",
        "max_tokens": 256,
    }
    second_response, second_latency = _request(client, settings, second_payload)
    content = _text_content(_message(second_response)).lower()
    if not any(marker in content for marker in ("37", "37,00", "37.00")):
        raise _ProbeError("tool_result_not_consumed")
    return {
        "latency_ms": first_latency + second_latency,
        "tool_result_minor": 3700,
        "answer_contains_expected_amount": True,
    }


def _image_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
    image_path: Path,
) -> dict[str, JsonValue]:
    """Send a local image without retaining its bytes or path in artifacts."""

    if not image_path.is_file():
        raise _ProbeError("image_file_missing")
    media_type, _ = mimetypes.guess_type(image_path.name)
    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise _ProbeError("unsupported_image_type")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload: dict[str, object] = {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": str(case.get("prompt", ""))},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                    },
                ],
            }
        ],
        "max_tokens": 128,
    }
    response, latency = _request(client, settings, payload)
    _text_content(_message(response))
    return {"latency_ms": latency, "accepted": True, "media_type": media_type}


SILENT_SLOT_THRESHOLD = 0.95
"""Share of unstated optional slots a candidate must leave empty.

Pre-registered before the first run. It is the weakest bar a downstream gate
could still rest on: text extraction allows a fabricated field on at most 1% of
cases, and tool selection needs an unstated period left out entirely, so a
shape scoring below this here cannot support either.
"""

STATED_SLOT_THRESHOLD = 0.95
"""Share of stated optional slots a candidate must capture.

Pre-registered with the threshold above and inseparable from it. A model that
empties every optional slot would otherwise screen as perfectly reliable while
discarding what the owner actually wrote.
"""

_ABSENT = object()
"""Marker distinguishing a property left out from one present and null."""


def _cases_by_capability(
    cases: list[dict[str, object]], capability: str
) -> list[dict[str, object]]:
    """Return every dataset case for a capability, in dataset order.

    Args:
        cases: Decoded dataset records.
        capability: Stable capability identifier.

    Returns:
        The capability's cases.

    Raises:
        _ProbeError: If the capability has no cases.
    """

    matches = [case for case in cases if case.get("capability") == capability]
    if not matches:
        raise _ProbeError(f"dataset_case_count:{capability}:0")
    return matches


def _require_every_property(node: object) -> object:
    """Mark every property of every object required.

    This repeats the local adapter's rendering rather than importing it. A
    screen that asked the component it informs how to shape a request could not
    report that the shape itself is the variable under test.

    Args:
        node: JSON Schema fragment.

    Returns:
        The fragment with each object requiring all of its properties.
    """

    if isinstance(node, dict):
        transformed = {
            key: _require_every_property(value) for key, value in node.items()
        }
        properties = transformed.get("properties")
        if isinstance(properties, dict):
            transformed["required"] = sorted(properties)
        return transformed
    if isinstance(node, list):
        return [_require_every_property(item) for item in node]
    return node


def _without_null_branch(prop: object) -> object | None:
    """Return a property schema with its null alternative removed.

    The outer annotations are carried onto the surviving branch. The two
    shapes must differ only in how a message's silence is expressed: if
    unwrapping also dropped the title and description a model reads as
    guidance, a difference in scores could be a difference in the prompt.

    Args:
        prop: Property schema that may be a two-branch nullable union.

    Returns:
        The single remaining branch, or None when the property was not a
        nullable union and must stay required.
    """

    if not isinstance(prop, dict):
        return None
    branches = prop.get("anyOf")
    if not isinstance(branches, list):
        return None
    kept: list[object] = [
        branch
        for branch in branches
        if not (isinstance(branch, dict) and branch.get("type") == "null")
    ]
    if len(kept) == len(branches) or len(kept) != 1:
        return None
    surviving = kept[0]
    if not isinstance(surviving, dict):
        return surviving
    annotations = {
        key: value
        for key, value in prop.items()
        if key in {"title", "description", "default"}
    }
    return {**annotations, **surviving}


def _strip_null_branches(node: object) -> object:
    """Replace nullable unions with their value branch and drop them from required.

    Args:
        node: JSON Schema fragment whose objects require every property.

    Returns:
        A fragment in which a nullable property becomes an optional one, so
        absence is the only way left to say that a message states nothing.
    """

    if isinstance(node, list):
        return [_strip_null_branches(item) for item in node]
    if not isinstance(node, dict):
        return node
    transformed: dict[str, object] = {
        key: _strip_null_branches(value)
        for key, value in node.items()
        if key != "required"
    }
    properties = node.get("properties")
    if isinstance(properties, dict):
        required: list[str] = []
        rewritten: dict[str, object] = {}
        for name, prop in properties.items():
            replacement = _without_null_branch(prop)
            if replacement is None:
                required.append(str(name))
                rewritten[str(name)] = _strip_null_branches(prop)
            else:
                rewritten[str(name)] = _strip_null_branches(replacement)
        transformed["properties"] = rewritten
        transformed["required"] = sorted(required)
    return transformed


def _draft_schema(shape: str) -> dict[str, object]:
    """Render the draft contract under one optionality shape.

    Args:
        shape: Either `required_nullable`, the shape the local adapter sends
            today, or `optional_absent`, in which an unstated property is left
            out rather than set to null.

    Returns:
        The JSON Schema for that shape.
    """

    rendered = cast(
        dict[str, object],
        _require_every_property(_OptionalFieldDraft.model_json_schema()),
    )
    if shape == "required_nullable":
        return rendered
    absent = cast(dict[str, object], _strip_null_branches(rendered))
    absent["required"] = sorted(ALWAYS_STATED_DRAFT_FIELDS)
    return absent


DRAFT_INSTRUCTIONS = """\
You read one message in which the owner of a personal finance assistant \
reports money they spent. The owner writes in Italian. Report only what the \
message states.

- amount_minor: the magnitude in euro cents as a positive integer. Twelve \
euro is 1200, and Italian writes decimals with a comma, so `12,50` is 1250.
- description: a short Italian description of what the money was for.
- occurred_at_text: the owner's own temporal words copied verbatim, for \
example `ieri` or `il 3 marzo`. Do not convert them into a date.
- merchant: the shop, brand, or business the message names. A product is not \
a merchant.
- payment_parts: one part for each payment method the owner names, in the \
owner's own words such as `carta`, `contanti`, or `buoni pasto`. Give a part \
its own amount only when the owner splits the payment across methods.

Never invent a value and never choose a default. Never write a placeholder \
such as `unknown`, `n/a`, or `sconosciuto` as if it were a value: it would be \
recorded as the merchant's name or as a payment method."""
"""Shared draft instructions, sized like the boundary this probe screens."""

SHAPE_INSTRUCTIONS = {
    "required_nullable": (
        "Every property is present in your answer. Set a property the message "
        "does not state to null, and use an empty list when the message names "
        "no payment method."
    ),
    "optional_absent": (
        "Leave out entirely any property the message does not state; only "
        "`description` is always present. Use an empty list when the message "
        "names no payment method."
    ),
}
"""How each shape expresses that a message states nothing for a property."""


def _normalized(text: str) -> str:
    """Collapse whitespace and case for comparing owner words."""

    return " ".join(text.split()).casefold()


def _slot_state(value: object) -> str:
    """Classify what a model put in one optional slot.

    The three ways of not leaving a slot empty are kept apart because a
    downstream contract treats them differently: empty text is refused and
    costs a retry, a placeholder word validates and is stored as if the owner
    had written it, and an invented value is indistinguishable from a real one.

    Args:
        value: Decoded slot value, or the absent marker.

    Returns:
        One of `empty`, `empty_text`, `placeholder_word`, or `filled`.
    """

    if value is _ABSENT or value is None:
        return "empty"
    if isinstance(value, str):
        text = " ".join(value.split())
        if not text:
            return "empty_text"
        return "placeholder_word" if text.casefold() in EMPTY_PLACEHOLDERS else "filled"
    if isinstance(value, list):
        if not value:
            return "empty"
        states = {
            _slot_state(item.get("method") if isinstance(item, dict) else item)
            for item in value
        }
        for state in ("empty_text", "placeholder_word"):
            if states == {state}:
                return state
        return "filled"
    return "filled"


def _texts_match(expected: str, actual: object) -> bool:
    """Compare owner words allowing a longer verbatim span to count.

    A model that copies `il 3 marzo` where the label records `3 marzo` has
    captured what the message stated. Containment therefore counts, because
    this metric exists to prove a slot is not emptied indiscriminately rather
    than to score span boundaries.
    """

    if not isinstance(actual, str):
        return False
    wanted, given = _normalized(expected), _normalized(actual)
    return bool(given) and (wanted == given or wanted in given or given in wanted)


def _payment_parts_match(expected: object, actual: object) -> bool:
    """Compare payment methods in order, and amounts only where stated."""

    if not isinstance(expected, list) or not isinstance(actual, list):
        return False
    if len(expected) != len(actual):
        return False
    for wanted, given in zip(expected, actual, strict=True):
        if not isinstance(wanted, dict) or not isinstance(given, dict):
            return False
        if not _texts_match(str(wanted.get("method", "")), given.get("method")):
            return False
        wanted_amount = wanted.get("amount_minor")
        if wanted_amount is not None and given.get("amount_minor") != wanted_amount:
            return False
    return True


def _stated_slot_matches(slot: str, expected: object, actual: object) -> bool:
    """Decide whether a slot the message states was captured."""

    if slot == "payment_parts":
        return _payment_parts_match(expected, actual)
    if slot == "amount_minor":
        return actual == expected
    return _texts_match(str(expected), actual)


def _draft_keys_permitted(decoded: Mapping[str, object], shape: str) -> bool:
    """Check that a draft answer carries only declared properties."""

    permitted = set(OPTIONAL_DRAFT_SLOTS) | set(ALWAYS_STATED_DRAFT_FIELDS)
    if not set(decoded) <= permitted:
        return False
    if not set(ALWAYS_STATED_DRAFT_FIELDS) <= set(decoded):
        return False
    if shape == "required_nullable":
        return permitted <= set(decoded)
    return True


def _omission_shape_result(
    client: httpx.Client,
    settings: CapabilitySettings,
    cases: list[dict[str, object]],
    shape: str,
) -> tuple[dict[str, float | int], int]:
    """Score every omission case under one optionality shape.

    Returns:
        The shape's scores and the total latency it spent.
    """

    schema = _draft_schema(shape)
    latency_total = 0
    invalid = 0
    silent_total = silent_empty = 0
    empty_text = placeholder_words = fabricated = 0
    stated_total = stated_captured = 0
    for case in cases:
        payload: dict[str, object] = {
            "model": settings.model_alias,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": f"{DRAFT_INSTRUCTIONS}\n\n{SHAPE_INSTRUCTIONS[shape]}",
                },
                {"role": "user", "content": str(case.get("prompt", ""))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "money_tracker_capability_draft",
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_tokens": 384,
        }
        response, latency = _request(client, settings, payload)
        latency_total += latency
        try:
            decoded = json.loads(_text_content(_message(response)))
        except ValueError:
            invalid += 1
            continue
        if not isinstance(decoded, dict) or not _draft_keys_permitted(decoded, shape):
            invalid += 1
            continue
        expected = as_object_mapping(case.get("expected"))
        for slot in OPTIONAL_DRAFT_SLOTS:
            wanted = expected.get(slot)
            given = decoded.get(slot, _ABSENT)
            if wanted is None or wanted == []:
                silent_total += 1
                state = _slot_state(given)
                if state == "empty":
                    silent_empty += 1
                elif state == "empty_text":
                    empty_text += 1
                elif state == "placeholder_word":
                    placeholder_words += 1
                else:
                    fabricated += 1
                continue
            stated_total += 1
            if _stated_slot_matches(slot, wanted, given):
                stated_captured += 1
    scores: dict[str, float | int] = {
        "schema_invalid_responses": invalid,
        "silent_slots": silent_total,
        "silent_slots_left_empty": round(silent_empty / silent_total, 4)
        if silent_total
        else 0.0,
        "silent_slots_given_empty_text": empty_text,
        "silent_slots_given_placeholder_word": placeholder_words,
        "silent_slots_fabricated": fabricated,
        "stated_slots": stated_total,
        "stated_slots_captured": round(stated_captured / stated_total, 4)
        if stated_total
        else 0.0,
    }
    return scores, latency_total


def _meets_omission_gate(scores: Mapping[str, float | int]) -> bool:
    """Decide whether one shape reached both pre-registered thresholds."""

    return (
        scores["schema_invalid_responses"] == 0
        and scores["silent_slots_left_empty"] >= SILENT_SLOT_THRESHOLD
        and scores["stated_slots_captured"] >= STATED_SLOT_THRESHOLD
    )


def _omission_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    cases: list[dict[str, object]],
) -> dict[str, JsonValue]:
    """Screen whether a candidate leaves an unstated optional field alone.

    Both optionality shapes are measured, because the failure three product
    boundaries share may belong to the shape rather than to the model. The
    probe passes when either shape reaches both pre-registered thresholds with
    no invalid answer, since a boundary is free to adopt the shape that works.
    """

    measured: dict[str, dict[str, float | int]] = {}
    latency_total = 0
    for shape in SHAPE_INSTRUCTIONS:
        scores, latency = _omission_shape_result(client, settings, cases, shape)
        measured[shape] = scores
        latency_total += latency
    return {
        "latency_ms": latency_total,
        "gate_passed": any(map(_meets_omission_gate, measured.values())),
        "cases": len(cases),
        "silent_slot_threshold": SILENT_SLOT_THRESHOLD,
        "stated_slot_threshold": STATED_SLOT_THRESHOLD,
        "shapes": cast(JsonValue, measured),
    }


def _union_schema(shape: str) -> dict[str, object]:
    """Render the two-variant category judgment under one union shape.

    Args:
        shape: Either `root_union`, where the whole answer is one variant, or
            `nested_union`, where the variant sits under a single property.

    Returns:
        The JSON Schema for that shape.
    """

    existing = cast(
        dict[str, object],
        _require_every_property(_ExistingCategoryChoice.model_json_schema()),
    )
    proposal = cast(
        dict[str, object],
        _require_every_property(_ProposedCategoryChoice.model_json_schema()),
    )
    alternatives = [existing, proposal]
    if shape == "root_union":
        return {"oneOf": alternatives}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"judgment": {"oneOf": alternatives}},
        "required": ["judgment"],
    }


UNION_INSTRUCTIONS = """\
You decide which spending purpose describes one movement, choosing from the \
owner's active category list below. Categories are named, never numbered: \
reply with a name exactly as it appears in that list.

Active categories: {categories}

Classify by the purpose of the spending, not by the shop that sold it and not \
by the payment method. Choose the existing outcome whenever any active \
category adequately describes the purpose. Choose the proposal outcome only \
when no active category is adequate, and then give the candidate a short \
Italian purpose name and say why no active category is adequate.

Your answer takes exactly one of the two shapes the schema offers. Never \
answer with a field belonging to the shape you did not choose."""
"""Union instructions, deliberately short of the domain suite's taxonomy rules."""

UNION_VARIANT_KEYS = {
    "existing": frozenset({"outcome", "category_name"}),
    "proposal": frozenset({"outcome", "proposed_name", "rationale"}),
}
"""Exact property set each variant may carry."""


def _meets_union_gate(scores: Mapping[str, JsonValue]) -> bool:
    """Decide whether one shape satisfied every mechanical expectation."""

    return (
        scores["schema_accepted"] is True
        and scores["schema_invalid_responses"] == 0
        and scores["answers_carrying_a_foreign_field"] == 0
        and scores["both_variants_reachable"] is True
    )


def _union_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    cases: list[dict[str, object]],
) -> dict[str, JsonValue]:
    """Screen whether a candidate can answer with a discriminated union.

    The blocking half is mechanical: the server accepts the schema, every
    answer matches exactly one variant, and no answer carries a field from the
    variant it did not choose. Which variant is correct is scored only as a
    diagnostic, because `categorization/purpose-it` owns that gate and two
    documents must not gate the same thing.
    """

    measured: dict[str, dict[str, JsonValue]] = {}
    latency_total = 0
    for shape in ("root_union", "nested_union"):
        schema = _union_schema(shape)
        accepted = True
        invalid = foreign_fields = 0
        selected_correctly = names_from_list = existing_answers = 0
        produced: set[str] = set()
        for case in cases:
            payload: dict[str, object] = {
                "model": settings.model_alias,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": UNION_INSTRUCTIONS.format(
                            categories=", ".join(
                                str(name)
                                for name in cast(
                                    list[object], case.get("active_categories", [])
                                )
                            )
                        ),
                    },
                    {"role": "user", "content": str(case.get("prompt", ""))},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "money_tracker_capability_category",
                        "strict": True,
                        "schema": schema,
                    },
                },
                "max_tokens": 256,
            }
            try:
                response, latency = _request(client, settings, payload)
            except _ProbeError as error:
                if not str(error).startswith("http_status:"):
                    raise
                accepted = False
                break
            latency_total += latency
            try:
                decoded = json.loads(_text_content(_message(response)))
            except ValueError:
                invalid += 1
                continue
            if shape == "nested_union" and isinstance(decoded, dict):
                decoded = decoded.get("judgment")
            if not isinstance(decoded, dict):
                invalid += 1
                continue
            outcome = decoded.get("outcome")
            if not isinstance(outcome, str) or outcome not in UNION_VARIANT_KEYS:
                invalid += 1
                continue
            produced.add(outcome)
            if set(decoded) != UNION_VARIANT_KEYS[outcome]:
                foreign_fields += 1
            expected = as_object_mapping(case.get("expected"))
            if outcome == expected.get("outcome"):
                selected_correctly += 1
            if outcome == "existing":
                existing_answers += 1
                permitted = {
                    _normalized(str(name))
                    for name in cast(list[object], case.get("active_categories", []))
                }
                if _normalized(str(decoded.get("category_name", ""))) in permitted:
                    names_from_list += 1
        measured[shape] = {
            "schema_accepted": accepted,
            "schema_invalid_responses": invalid,
            "answers_carrying_a_foreign_field": foreign_fields,
            "both_variants_reachable": produced == set(UNION_VARIANT_KEYS),
            "variant_selected_correctly": round(selected_correctly / len(cases), 4)
            if cases
            else 0.0,
            "existing_names_from_supplied_list": round(
                names_from_list / existing_answers, 4
            )
            if existing_answers
            else 0.0,
            "scored_answers": len(cases) - invalid if accepted else 0,
        }
    return {
        "latency_ms": latency_total,
        "gate_passed": any(map(_meets_union_gate, measured.values())),
        "cases": len(cases),
        "shapes": cast(JsonValue, measured),
    }


def _execute_probe(name: str, probe: ProbeCallable) -> ProbeResult:
    """Convert one probe into a safe result without leaking response content.

    A probe that could not run at all reports `error`; a probe that ran and
    missed its pre-registered threshold reports `failed` through the
    `gate_passed` key. Keeping the two apart matters here, because a screen
    whose report cannot distinguish an unreachable server from a measured
    refusal would answer the wrong question.
    """

    started = time.monotonic()
    try:
        details = probe()
    except (httpx.HTTPError, OSError, ValueError, TypeError, _ProbeError) as error:
        safe_error = (
            str(error) if isinstance(error, _ProbeError) else type(error).__name__
        )
        return ProbeResult(
            name=name,
            status=ProbeStatus.ERROR,
            latency_ms=round((time.monotonic() - started) * 1000),
            details={"error": safe_error},
        )
    latency = details.pop("latency_ms", None)
    gate_passed = details.get("gate_passed", True)
    return ProbeResult(
        name=name,
        status=ProbeStatus.PASSED if gate_passed is not False else ProbeStatus.FAILED,
        latency_ms=int(latency) if isinstance(latency, int) else None,
        details=details,
    )


def _server_properties(
    client: httpx.Client,
    settings: CapabilitySettings,
) -> tuple[Mapping[str, object] | None, ProbeResult]:
    """Read llama.cpp properties through its real local HTTP endpoint."""

    started = time.monotonic()
    try:
        response = client.get(
            f"{settings.server_root_url}/props",
            params={"model": settings.model_alias},
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )
        if response.status_code >= 400:
            raise _ProbeError(f"http_status:{response.status_code}")
        properties = as_object_mapping(response.json())
    except (httpx.HTTPError, ValueError, TypeError, _ProbeError) as error:
        safe_error = (
            str(error) if isinstance(error, _ProbeError) else type(error).__name__
        )
        return None, ProbeResult(
            name="server_metadata",
            status=ProbeStatus.ERROR,
            latency_ms=round((time.monotonic() - started) * 1000),
            details={"error": safe_error},
        )
    raw_modalities = properties.get("modalities")
    modalities = (
        [value for value in raw_modalities if isinstance(value, str)]
        if isinstance(raw_modalities, list)
        else []
    )
    return properties, ProbeResult(
        name="server_metadata",
        status=ProbeStatus.PASSED,
        latency_ms=round((time.monotonic() - started) * 1000),
        details={
            "has_chat_template": bool(
                properties.get("chat_template_tool_use")
                or properties.get("chat_template")
            ),
            "modalities": cast(list[JsonValue], modalities),
        },
    )


def _write_json(path: Path, value: object) -> None:
    """Atomically write stable UTF-8 JSON evaluation output."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_capability_suite(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    image_path: Path | None = None,
    publish_langfuse: bool = False,
) -> CapabilityRunResult:
    """Execute capability probes and always emit a local deterministic report.

    Args:
        settings: Local inference endpoint and artifact metadata settings.
        dataset_path: Committed synthetic JSONL capability dataset.
        output_root: Ignored local directory for raw run artifacts.
        image_path: Optional local image used only for a multimodal request.
        publish_langfuse: Publish a redacted aggregate trace to local Langfuse.

    Returns:
        Typed report and artifact directory. A failing server is represented in
        the report rather than silently skipped.
    """

    started = datetime.now(UTC)
    run_id = f"CAP-{started.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    repository_root = Path(__file__).resolve().parents[3]
    cases = _load_cases(dataset_path)
    dataset_version = _dataset_version(cases)

    with httpx.Client(timeout=settings.timeout_seconds) as client:
        properties, metadata_probe = _server_properties(client, settings)
        probes = [metadata_probe]
        probes.append(
            _execute_probe(
                "strict_structured_output",
                lambda: _structured_probe(
                    client,
                    settings,
                    _case_by_capability(cases, "strict_structured_output"),
                ),
            )
        )
        probes.append(
            _execute_probe(
                "tool_calling",
                lambda: _tool_call_probe(
                    client,
                    settings,
                    _case_by_capability(cases, "tool_calling"),
                ),
            )
        )
        probes.append(
            _execute_probe(
                "tool_result_sequencing",
                lambda: _tool_sequence_probe(
                    client,
                    settings,
                    _case_by_capability(cases, "tool_result_sequencing"),
                ),
            )
        )
        probes.append(
            _execute_probe(
                "optional_field_omission",
                lambda: _omission_probe(
                    client,
                    settings,
                    _cases_by_capability(cases, "optional_field_omission"),
                ),
            )
        )
        probes.append(
            _execute_probe(
                "union_output",
                lambda: _union_probe(
                    client,
                    settings,
                    _cases_by_capability(cases, "union_output"),
                ),
            )
        )
        if image_path is None:
            probes.append(
                ProbeResult(
                    name="image_input",
                    status=ProbeStatus.NOT_APPLICABLE,
                    details={"reason": "no_image_fixture_supplied"},
                )
            )
        else:
            probes.append(
                _execute_probe(
                    "image_input",
                    lambda: _image_probe(
                        client,
                        settings,
                        _case_by_capability(cases, "image_input"),
                        image_path,
                    ),
                )
            )

    model_metadata = collect_model_metadata(
        settings,
        properties,
        vision_required=image_path is not None,
    )
    required = {
        "server_metadata",
        "strict_structured_output",
        "tool_calling",
        "tool_result_sequencing",
        "optional_field_omission",
        "union_output",
    }
    if image_path is not None:
        required.add("image_input")
    blocking_passed = model_metadata.metadata_complete and all(
        probe.status is ProbeStatus.PASSED for probe in probes if probe.name in required
    )

    artifact_directory = (
        output_root
        / model_key(
            model_metadata,
            candidate_catalog(),
            modality="vision" if image_path is not None else "text",
        )
        / run_id
    )
    artifact_directory.mkdir(parents=True, exist_ok=False)
    try:
        artifact_reference = str(artifact_directory.relative_to(repository_root))
    except ValueError:
        artifact_reference = str(artifact_directory)
    completed = datetime.now(UTC)
    report = CapabilityReport(
        run_id=run_id,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        source=collect_source_metadata(repository_root),
        dataset=DatasetMetadata(
            suite="capabilities/local-model",
            dataset_id="capabilities-local-model",
            version=dataset_version,
            content_hash=sha256_file(dataset_path),
            item_count=len(cases),
        ),
        model=model_metadata,
        environment=collect_environment_metadata(),
        probes=probes,
        blocking_gates_passed=blocking_passed,
        local_artifact_path=artifact_reference,
    )

    if publish_langfuse:
        from money_tracker.evaluations.publication import publish_capability_report

        report = report.model_copy(
            update={"langfuse_published": publish_capability_report(report)}
        )

    _write_json(artifact_directory / "manifest.json", report.model_dump(mode="json"))
    _write_json(
        artifact_directory / "scores.json",
        {
            "run_id": run_id,
            "blocking_gates_passed": blocking_passed,
            "metadata_complete": model_metadata.metadata_complete,
            "scores": {
                probe.name: probe.status is ProbeStatus.PASSED for probe in probes
            },
        },
    )
    return CapabilityRunResult(report=report, artifact_directory=artifact_directory)
