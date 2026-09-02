"""Tests for explicit receipt-archive runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from money_tracker.media import ReceiptArchiveSettings


def test_receipt_archive_root_is_required() -> None:
    """Refuse an implicit archive location for authoritative private media."""

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="RECEIPT_ARCHIVE_ROOT is required"):
            ReceiptArchiveSettings.from_env()


def test_receipt_archive_root_must_be_absolute() -> None:
    """Reject working-directory-dependent storage paths."""

    with patch.dict(
        os.environ,
        {"MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT": "relative/receipts"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="must be absolute"):
            ReceiptArchiveSettings.from_env()


def test_receipt_archive_root_preserves_explicit_absolute_path(tmp_path: Path) -> None:
    """Return the owner-selected absolute path without creating it yet."""

    root = tmp_path / "private-receipts"
    with patch.dict(
        os.environ,
        {"MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT": str(root)},
        clear=True,
    ):
        settings = ReceiptArchiveSettings.from_env()

    assert settings.root == root
    assert not root.exists()
