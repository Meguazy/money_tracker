"""Opt-in integration check against the real Telegram test bot."""

from __future__ import annotations

import os

import pytest

from money_tracker.messaging import BotEnvironment
from money_tracker.telegram import BotApiClient, TelegramSettings

pytestmark = pytest.mark.telegram


def test_real_test_bot_validates_and_preserves_pending_updates() -> None:
    """Exercise production HTTPS validation and webhook removal on the test bot."""

    if os.getenv("RUN_TELEGRAM_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_TELEGRAM_INTEGRATION_TESTS=1 for the real test bot")
    settings = TelegramSettings.from_env()
    if settings.environment is not BotEnvironment.TEST:
        raise RuntimeError("real Telegram integration is restricted to the test bot")
    with BotApiClient(settings.read_token(), settings) as transport:
        identity = transport.validate_bot()
        transport.disable_webhook()
    assert identity.bot_id > 0
