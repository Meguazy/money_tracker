"""Unit tests for stable local-model capability request contracts."""

from __future__ import annotations

import pytest

from money_tracker.evaluations.capabilities import (
    _dataset_version,
    _ProbeError,
    _tool_request_payload,
)
from money_tracker.evaluations.metadata import CapabilitySettings


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
