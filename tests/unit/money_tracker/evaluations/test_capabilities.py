"""Unit tests for stable local-model capability request contracts."""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from money_tracker.evaluations.capabilities import (
    ALWAYS_STATED_DRAFT_FIELDS,
    OPTIONAL_DRAFT_SLOTS,
    UNION_VARIANT_KEYS,
    _cases_by_capability,
    _dataset_version,
    _draft_keys_permitted,
    _draft_schema,
    _execute_probe,
    _ProbeError,
    _slot_state,
    _stated_slot_matches,
    _tool_request_payload,
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
