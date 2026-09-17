"""Tests for the explicit tracing switch and its local-only endpoint rule."""

from __future__ import annotations

import pytest

from money_tracker.tracing import (
    DISABLED_TRACER,
    TracingConfigurationError,
    TracingSettings,
    open_tracer,
)

_KEYS = {
    "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
    "LANGFUSE_SECRET_KEY": "sk-lf-test-secret-value",
    "LANGFUSE_BASE_URL": "http://127.0.0.1:3000",
}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Provide project keys and a loopback endpoint, with the switch unset."""

    monkeypatch.delenv("MONEY_TRACKER_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LANGFUSE_TRACING_ENVIRONMENT", raising=False)
    for name, value in _KEYS.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_project_keys_alone_do_not_enable_tracing(
    configured: pytest.MonkeyPatch,
) -> None:
    """Keep a restarted service untraced until the operator opts in.

    Every host unit reads `.env`, where Langfuse initialization already wrote
    the project keys, so their presence cannot be the switch.
    """

    assert TracingSettings.from_env() == TracingSettings.disabled()


@pytest.mark.parametrize("value", ["false", "0", "off", "no", ""])
def test_false_values_disable_tracing(
    configured: pytest.MonkeyPatch, value: str
) -> None:
    """Accept the usual spellings of off."""

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", value)

    assert not TracingSettings.from_env().enabled


def test_enabled_settings_carry_the_local_store_configuration(
    configured: pytest.MonkeyPatch,
) -> None:
    """Read keys, endpoint, environment, and the export bound together."""

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    configured.setenv("LANGFUSE_TRACING_ENVIRONMENT", "integration-test")
    configured.setenv("MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS", "3")

    settings = TracingSettings.from_env()

    assert settings.enabled
    assert settings.base_url == "http://127.0.0.1:3000"
    assert settings.environment == "integration-test"
    assert settings.export_timeout_seconds == 3
    assert "sk-lf-test-secret-value" not in repr(settings)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "https://cloud.langfuse.com",
        "http://192.168.1.20:3000",
        "http://user:secret@localhost:3000",
        "ftp://localhost:3000",
    ],
)
def test_a_non_local_store_is_refused(
    configured: pytest.MonkeyPatch, base_url: str
) -> None:
    """Never send full-fidelity traces off the workstation.

    The SDK's own default endpoint is a cloud service, so an unset URL is
    refused rather than defaulted.
    """

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    configured.setenv("LANGFUSE_BASE_URL", base_url)

    with pytest.raises(TracingConfigurationError) as raised:
        TracingSettings.from_env()

    assert raised.value.reason_code == "non_loopback_endpoint"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_hosts_are_accepted(configured: pytest.MonkeyPatch, host: str) -> None:
    """Admit every loopback spelling."""

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    configured.setenv("LANGFUSE_BASE_URL", f"http://{host}:3000/")

    assert TracingSettings.from_env().base_url == f"http://{host}:3000"


def test_missing_keys_are_refused(configured: pytest.MonkeyPatch) -> None:
    """Name the missing settings without echoing any value."""

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    configured.delenv("LANGFUSE_SECRET_KEY")

    with pytest.raises(TracingConfigurationError) as raised:
        TracingSettings.from_env()

    assert raised.value.reason_code == "missing_project_keys"
    assert "pk-lf-test" not in str(raised.value)


@pytest.mark.parametrize(
    ("name", "value", "reason"),
    [
        ("MONEY_TRACKER_TRACING_ENABLED", "maybe", "invalid_switch"),
        ("MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS", "0", "invalid_export_timeout"),
        (
            "MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS",
            "1.5",
            "invalid_export_timeout",
        ),
    ],
)
def test_unreadable_values_are_refused(
    configured: pytest.MonkeyPatch, name: str, value: str, reason: str
) -> None:
    """Refuse a value that cannot be honored exactly."""

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    configured.setenv(name, value)

    with pytest.raises(TracingConfigurationError) as raised:
        TracingSettings.from_env()

    assert raised.value.reason_code == reason


def test_rejected_configuration_degrades_to_the_disabled_tracer(
    configured: pytest.MonkeyPatch,
) -> None:
    """Never let tracing configuration stop an entry point from running."""

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "true")
    configured.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    assert open_tracer() is DISABLED_TRACER


def test_disabled_settings_yield_the_disabled_tracer() -> None:
    """Return the shared tracer that records nothing."""

    tracer = open_tracer(TracingSettings.disabled())

    assert tracer is DISABLED_TRACER
    assert not tracer.enabled
    assert tracer.graph_config(graph="router") == {}


def test_publication_settings_ignore_the_runtime_switch_but_not_the_rules(
    configured: pytest.MonkeyPatch,
) -> None:
    """Let an explicit publication command publish while services stay untraced.

    The runtime switch governs what host services export; a publication is
    requested by its own command. The keys, loopback rule, and export bound
    still apply.
    """

    configured.setenv("MONEY_TRACKER_TRACING_ENABLED", "false")

    settings = TracingSettings.for_publication(environment="evaluation")

    assert settings.enabled is True
    assert settings.environment == "evaluation"
    assert settings.base_url == "http://127.0.0.1:3000"
    configured.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    with pytest.raises(TracingConfigurationError) as refused:
        TracingSettings.for_publication(environment="evaluation")
    assert refused.value.reason_code == "non_loopback_endpoint"
    configured.delenv("LANGFUSE_SECRET_KEY")
    with pytest.raises(TracingConfigurationError) as missing:
        TracingSettings.for_publication(environment="evaluation")
    assert missing.value.reason_code == "missing_project_keys"
