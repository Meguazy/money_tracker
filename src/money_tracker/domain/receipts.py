"""Receipt metadata contract independent of extraction implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from money_tracker.domain.errors import DomainError

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReceiptStatus(StrEnum):
    """Metadata lifecycle before the evaluated extraction workflow exists."""

    STORED = "stored"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class ReceiptMetadata:
    """Private content-addressed media reference and review state.

    The storage key is the lowercase SHA-256 digest, not a user-controlled path.
    Image bytes and filesystem locations stay behind the media-store port.

    Attributes:
        id: Stable receipt identifier.
        profile_id: Owner scope.
        content_sha256: Lowercase content digest and storage key.
        media_type: Validated decoded media type.
        byte_size: Original byte count.
        stored_at: Time the private archive accepted the content.
        source_reference: Stable API, CLI, or future Telegram source reference.
        status: Retention and review state.
        original_filename: Sanitized display metadata, never a storage path.
    """

    id: UUID
    profile_id: UUID
    content_sha256: str
    media_type: str
    byte_size: int
    stored_at: datetime
    source_reference: str
    status: ReceiptStatus = ReceiptStatus.STORED
    original_filename: str | None = None

    def __post_init__(self) -> None:
        """Validate safe metadata without inspecting image content."""

        if SHA256_PATTERN.fullmatch(self.content_sha256) is None:
            raise DomainError("receipt content hash must be lowercase SHA-256")
        if self.media_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise DomainError("unsupported receipt media type")
        if self.byte_size <= 0:
            raise DomainError("receipt byte size must be positive")
        if self.stored_at.tzinfo is None or self.stored_at.utcoffset() is None:
            raise DomainError("receipt storage time must include a timezone")
        if not self.source_reference.strip():
            raise DomainError("receipt requires a source reference")
        if self.original_filename is not None:
            if not self.original_filename.strip():
                raise DomainError("receipt filename cannot be empty")
            if "/" in self.original_filename or "\\" in self.original_filename:
                raise DomainError("receipt filename cannot contain path separators")
