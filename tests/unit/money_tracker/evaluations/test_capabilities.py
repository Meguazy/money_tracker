"""Unit tests for stable local-model capability request contracts."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from pydantic import JsonValue

from money_tracker.evaluations.capabilities import (
    ALWAYS_STATED_DRAFT_FIELDS,
    OPTIONAL_DRAFT_SLOTS,
    UNION_VARIANT_KEYS,
    _answer_text,
    _cases_by_capability,
    _dataset_version,
    _draft_keys_permitted,
    _draft_schema,
    _execute_probe,
    _ProbeError,
    _slot_state,
    _stated_slot_matches,
    _tool_request_payload,
    _tool_sequence_probe,
    _union_probe,
    _union_schema,
)
from money_tracker.evaluations.metadata import CapabilitySettings
from money_tracker.evaluations.models import ProbeStatus


def test_tool_probe_uses_supported_single_required_tool_contract() -> None:
    """Force the only tool using llama.cpp's supported string value."""

    settings = CapabilitySettings(
        base_url="http://127.0.0.1:8080/v1",
        api_key="local",
        model_alias="synthetic",
        timeout_seconds=1,
        model_path=None,
        server_binary=None,
        vision_projector_path=None,
        context_size=8192,
    )

    payload = _tool_request_payload(settings, "Use the tool")

    assert payload["tool_choice"] == "required"
    assert payload["max_tokens"] == 256
    tools = payload["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1


def test_dataset_version_requires_one_version_on_every_case() -> None:
    """Reject missing or mixed versions instead of mislabelling a run."""

    assert _dataset_version([{"dataset_version": "1.1.0"}]) == "1.1.0"
    with pytest.raises(_ProbeError, match="dataset_version_not_unique"):
        _dataset_version(
            [
                {"dataset_version": "1.0.0"},
                {"dataset_version": "1.1.0"},
            ]
        )
    with pytest.raises(_ProbeError, match="dataset_version_missing"):
        _dataset_version([{"dataset_version": "1.1.0"}, {}])


def test_optional_absent_shape_makes_absence_the_only_empty_form() -> None:
    """Contrast the two optionality shapes the omission probe compares."""

    nullable = _draft_schema("required_nullable")
    absent = _draft_schema("optional_absent")

    assert nullable["required"] == sorted(
        set(OPTIONAL_DRAFT_SLOTS) | set(ALWAYS_STATED_DRAFT_FIELDS)
    )
    assert absent["required"] == sorted(ALWAYS_STATED_DRAFT_FIELDS)
    nullable_properties = nullable["properties"]
    absent_properties = absent["properties"]
    assert isinstance(nullable_properties, dict)
    assert isinstance(absent_properties, dict)
    assert nullable_properties["merchant"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert absent_properties["merchant"] == {"title": "Merchant", "type": "string"}


def test_slot_state_separates_the_three_ways_of_not_leaving_a_slot_empty() -> None:
    """Empty text is refused downstream; a placeholder word is stored as a value."""

    assert _slot_state(None) == "empty"
    assert _slot_state([]) == "empty"
    assert _slot_state("Esselunga") == "filled"
    assert _slot_state([{"method": "carta"}]) == "filled"
    for blank in ("", "   "):
        assert _slot_state(blank) == "empty_text"
    for word in ("n/a", "sconosciuto", "Unknown"):
        assert _slot_state(word) == "placeholder_word"
    assert _slot_state([{"method": ""}]) == "empty_text"
    assert _slot_state([{"method": "n/a"}]) == "placeholder_word"


def test_stated_slot_matching_accepts_a_longer_verbatim_span() -> None:
    """Credit a captured slot without scoring where the owner's span ends."""

    assert _stated_slot_matches("occurred_at_text", "3 marzo", "il 3 marzo")
    assert _stated_slot_matches("merchant", "Coop", "COOP")
    assert not _stated_slot_matches("merchant", "Coop", None)
    assert _stated_slot_matches("amount_minor", 1250, 1250)
    assert not _stated_slot_matches("amount_minor", 1250, 1200)


def test_payment_part_amounts_are_compared_only_where_the_message_states_them() -> None:
    """A single named method has no amount of its own to check."""

    assert _stated_slot_matches(
        "payment_parts",
        [{"method": "buoni pasto", "amount_minor": None}],
        [{"method": "buoni pasto", "amount_minor": None}],
    )
    assert _stated_slot_matches(
        "payment_parts",
        [{"method": "buoni pasto", "amount_minor": None}],
        [{"method": "buoni pasto", "amount_minor": 1200}],
    )
    assert not _stated_slot_matches(
        "payment_parts",
        [
            {"method": "carta", "amount_minor": 3000},
            {"method": "contanti", "amount_minor": 2000},
        ],
        [
            {"method": "carta", "amount_minor": 3000},
            {"method": "contanti", "amount_minor": 1500},
        ],
    )


def test_draft_keys_are_permitted_per_shape() -> None:
    """Require every property only where the shape says every one is present."""

    complete = {
        "amount_minor": None,
        "description": "benzina",
        "occurred_at_text": None,
        "merchant": None,
        "payment_parts": [],
    }

    assert _draft_keys_permitted(complete, "required_nullable")
    assert _draft_keys_permitted(complete, "optional_absent")
    assert not _draft_keys_permitted({"description": "benzina"}, "required_nullable")
    assert _draft_keys_permitted({"description": "benzina"}, "optional_absent")
    assert not _draft_keys_permitted({"amount_minor": 1}, "optional_absent")
    assert not _draft_keys_permitted(
        {**complete, "category": "alimentari"}, "optional_absent"
    )


def test_union_shapes_offer_exactly_the_two_variants() -> None:
    """Both shapes carry the same alternatives, at the root or under a property."""

    root = _union_schema("root_union")
    nested = _union_schema("nested_union")

    assert set(root) == {"oneOf"}
    assert isinstance(root["oneOf"], list)
    assert len(root["oneOf"]) == 2
    nested_properties = nested["properties"]
    assert isinstance(nested_properties, dict)
    assert nested_properties["judgment"]["oneOf"] == root["oneOf"]
    assert UNION_VARIANT_KEYS["existing"].isdisjoint(
        UNION_VARIANT_KEYS["proposal"] - {"outcome"}
    )


def test_a_measured_refusal_is_reported_as_failed_not_as_an_error() -> None:
    """Keep an unreachable server distinguishable from a missed threshold."""

    passed = _execute_probe("probe", lambda: {"gate_passed": True, "latency_ms": 3})
    failed = _execute_probe("probe", lambda: {"gate_passed": False, "latency_ms": 5})

    def unreachable() -> dict[str, JsonValue]:
        raise _ProbeError("http_status:503")

    errored = _execute_probe("probe", unreachable)

    assert passed.status is ProbeStatus.PASSED
    assert failed.status is ProbeStatus.FAILED
    assert failed.details["gate_passed"] is False
    assert errored.status is ProbeStatus.ERROR
    assert errored.details == {"error": "http_status:503"}


def test_a_capability_with_no_cases_is_refused() -> None:
    """Never screen a capability the dataset does not actually cover."""

    with pytest.raises(_ProbeError, match="dataset_case_count:union_output:0"):
        _cases_by_capability([{"capability": "tool_calling"}], "union_output")


def _settings() -> CapabilitySettings:
    """Build endpoint settings for a probe driven by a scripted transport."""

    return CapabilitySettings(
        base_url="http://127.0.0.1:8080/v1",
        api_key="local",
        model_alias="synthetic",
        timeout_seconds=5,
        model_path=None,
        server_binary=None,
        vision_projector_path=None,
        context_size=8192,
    )


def _client(replies: Iterator[dict[str, object]]) -> httpx.Client:
    """Answer each chat request with the next scripted assistant message."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": next(replies)}]})

    return httpx.Client(transport=httpx.MockTransport(handle))


def _union_case() -> dict[str, object]:
    """Build one union case with the fields the probe reads."""

    return {
        "capability": "union_output",
        "prompt": "Ho speso 60 euro di spesa",
        "active_categories": ["alimentari"],
        "expected": {"outcome": "existing", "category_name": "alimentari"},
    }


def test_an_answer_the_model_never_produced_is_a_value_not_an_exception() -> None:
    """Separate an empty answer from a broken exchange at the source."""

    assert _answer_text({"content": " hello "}) == "hello"
    assert _answer_text({"content": ""}) is None
    assert _answer_text({"content": None}) is None
    assert _answer_text({}) is None


def test_a_case_the_model_never_answered_is_counted_not_fatal() -> None:
    """Keep the other cases measured when one exhausts its token budget."""

    answered = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal answered
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        judgment = {"outcome": "existing", "category_name": "alimentari"}
        nested = "judgment" in schema.get("properties", {})
        answered += 1
        # Every shape answers its first case and runs out of budget on the
        # second, which is what a real candidate did on one union case.
        if answered % 2 == 0:
            message: dict[str, object] = {
                "role": "assistant",
                "content": "",
                "reasoning_content": "...",
            }
        else:
            message = {
                "role": "assistant",
                "content": json.dumps({"judgment": judgment} if nested else judgment),
            }
        return httpx.Response(200, json={"choices": [{"message": message}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        details = _union_probe(client, _settings(), [_union_case(), _union_case()])

    shapes = details["shapes"]
    assert isinstance(shapes, dict)
    for shape in ("root_union", "nested_union"):
        assert shapes[shape]["answers_not_produced"] == 1
        assert shapes[shape]["schema_invalid_responses"] == 1
        assert shapes[shape]["scored_answers"] == 1
        assert shapes[shape]["schema_accepted"] is True
    assert details["gate_passed"] is False


def test_a_tool_result_read_as_euros_fails_and_says_which_defect_it_was() -> None:
    """Name a hundredfold unit error rather than calling it an ignored result."""

    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "get_budget_remaining",
            "arguments": json.dumps({"category_id": "subscriptions"}),
        },
    }
    replies = iter(
        [
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {
                "role": "assistant",
                "content": "Resta un budget di **3.700 EUR** per subscriptions.",
            },
        ]
    )
    with _client(replies) as client:
        details = _tool_sequence_probe(client, _settings(), {"prompt": "Quanto resta?"})

    assert details["gate_passed"] is False
    assert details["answer_produced"] is True
    assert details["answer_contains_expected_amount"] is False
    assert details["answer_contains_unconverted_minor_units"] is True
    assert _execute_probe("tool_result_sequencing", lambda: dict(details)).status is (
        ProbeStatus.FAILED
    )


def test_a_converted_tool_result_still_passes() -> None:
    """Keep the success path intact for a candidate that reads minor units."""

    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "get_budget_remaining",
            "arguments": json.dumps({"category_id": "subscriptions"}),
        },
    }
    replies = iter(
        [
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "assistant", "content": "Restano 37,00 EUR."},
        ]
    )
    with _client(replies) as client:
        details = _tool_sequence_probe(client, _settings(), {"prompt": "Quanto resta?"})

    assert details["gate_passed"] is True
    assert details["answer_contains_unconverted_minor_units"] is False
