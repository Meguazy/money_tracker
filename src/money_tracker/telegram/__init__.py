"""Secure Telegram Bot API gateway components."""

from money_tracker.telegram.config import TelegramSettings
from money_tracker.telegram.contracts import (
    BotIdentity,
    IngestReport,
    TelegramFile,
    TelegramInboundUpdate,
    TelegramSentMessage,
)
from money_tracker.telegram.gateway import TelegramGateway
from money_tracker.telegram.ingestion import TelegramUpdateIngestor
from money_tracker.telegram.transport import (
    BotApiClient,
    TelegramTransport,
    TelegramTransportError,
)

__all__ = [
    "BotApiClient",
    "BotIdentity",
    "IngestReport",
    "TelegramFile",
    "TelegramGateway",
    "TelegramInboundUpdate",
    "TelegramSentMessage",
    "TelegramSettings",
    "TelegramTransport",
    "TelegramTransportError",
    "TelegramUpdateIngestor",
]
