"""Runtime configuration for the private receipt archive."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReceiptArchiveSettings:
    """Resolve the explicitly configured private original-media root.

    Attributes:
        root: Absolute application-owned archive directory outside web roots.
    """

    root: Path

    @classmethod
    def from_env(cls) -> ReceiptArchiveSettings:
        """Load and validate ``MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT``.

        Returns:
            Validated absolute archive configuration.

        Raises:
            RuntimeError: If the setting is absent or not absolute.
        """

        raw_value = os.getenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", "").strip()
        if not raw_value:
            raise RuntimeError("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT is required")
        root = Path(raw_value)
        if not root.is_absolute():
            raise RuntimeError("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT must be absolute")
        return cls(root=root)
