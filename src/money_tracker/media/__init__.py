"""Private receipt-media adapters."""

from money_tracker.media.config import ReceiptArchiveSettings
from money_tracker.media.local import LocalReceiptMediaStore

__all__ = ["LocalReceiptMediaStore", "ReceiptArchiveSettings"]
