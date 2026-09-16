"""Privacy tests for local inference client composition."""

from __future__ import annotations

import logging

import pytest

from money_tracker.config import Settings
from money_tracker.llm import create_chat_model


def test_inference_client_log_omits_the_configured_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep provider URLs and credentials out of the operational journal."""

    endpoint = "http://private-inference.example.invalid:8080/v1"
    settings = Settings(
        llm_base_url=endpoint,
        llm_api_key="private-local-key",
        llm_model="promoted-model",
        llm_temperature=0.1,
        llm_timeout_seconds=120.0,
    )
    caplog.set_level(logging.INFO, logger="money_tracker.llm")

    create_chat_model(settings)

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "inference.client.created"
    ]
    assert len(records) == 1
    fields = records[0].money_tracker_fields
    assert fields["model"] == "promoted-model"
    assert "base_url" not in fields
    assert endpoint not in caplog.text
    assert settings.llm_api_key not in caplog.text
