"""Tests for bounded local administration file input."""

from __future__ import annotations

from pathlib import Path

import pytest

from money_tracker.administration import _read_private_input
from money_tracker.application import ReceiptMediaError
from money_tracker.media.local import MAX_RECEIPT_BYTES


def test_read_private_input_returns_bounded_regular_file(tmp_path: Path) -> None:
    """Read an explicitly selected regular file without altering it."""

    receipt = tmp_path / "receipt.png"
    receipt.write_bytes(b"content")

    assert _read_private_input(receipt) == b"content"


def test_read_private_input_rejects_final_symlink(tmp_path: Path) -> None:
    """Do not follow a final input symlink chosen by an untrusted caller."""

    target = tmp_path / "target.png"
    target.write_bytes(b"content")
    link = tmp_path / "receipt.png"
    link.symlink_to(target)

    with pytest.raises(ReceiptMediaError, match="safe regular file"):
        _read_private_input(link)


def test_read_private_input_rejects_directory(tmp_path: Path) -> None:
    """Translate a directory read attempt into the typed media error."""

    with pytest.raises(ReceiptMediaError, match="safe regular file"):
        _read_private_input(tmp_path)


def test_read_private_input_rejects_oversized_file(tmp_path: Path) -> None:
    """Apply the encoded-byte limit before passing content to the decoder."""

    receipt = tmp_path / "large.png"
    receipt.write_bytes(b"x" * (MAX_RECEIPT_BYTES + 1))

    with pytest.raises(ReceiptMediaError, match="byte limit"):
        _read_private_input(receipt)
