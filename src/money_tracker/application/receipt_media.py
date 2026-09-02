"""Replaceable contract for validated private receipt media storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredReceiptMedia:
    """Safe facts derived from decoded content stored under its SHA-256 digest.

    Attributes:
        content_sha256: Lowercase digest used as the application-controlled key.
        media_type: MIME type detected from decoded content.
        byte_size: Exact original payload size.
        width: Decoded image width in pixels.
        height: Decoded image height in pixels.
    """

    content_sha256: str
    media_type: str
    byte_size: int
    width: int
    height: int


class ReceiptMediaStore(Protocol):
    """Validate and retain original receipt images outside presentation roots."""

    def store(self, content: bytes) -> StoredReceiptMedia:
        """Store one validated image idempotently by content digest."""

    def read(self, content_sha256: str) -> bytes:
        """Read one retained original after verifying its content digest."""
