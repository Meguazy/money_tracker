"""Safe content-addressed filesystem storage for original receipt images."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from money_tracker.application import ReceiptMediaError, StoredReceiptMedia
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.media.local")

MAX_RECEIPT_BYTES = 20 * 1024 * 1024
MAX_RECEIPT_PIXELS = 40_000_000
MAX_RECEIPT_DIMENSION = 10_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORMAT_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class LocalReceiptMediaStore:
    """Validate and atomically retain originals in a private local archive.

    The archive owns its root and two-character digest shard directories. Files
    have no user-controlled name or extension: ``<root>/<sha[:2]>/<sha>``.
    Existing content is never overwritten.
    """

    def __init__(self, root: Path) -> None:
        """Prepare an application-owned mode-0700 archive directory.

        Args:
            root: Dedicated private archive root outside any web document root.

        Raises:
            ReceiptMediaError: If the root is a symlink or not a directory.
        """

        candidate = root.absolute()
        if candidate.is_symlink():
            raise ReceiptMediaError("receipt archive root cannot be a symlink")
        try:
            candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise ReceiptMediaError(
                "receipt archive root must be a real directory"
            ) from error
        if not candidate.is_dir() or candidate.is_symlink():
            raise ReceiptMediaError("receipt archive root must be a real directory")
        try:
            os.chmod(candidate, 0o700)
        except OSError as error:
            raise ReceiptMediaError("receipt archive root cannot be secured") from error
        self._root = candidate

    def store(self, content: bytes) -> StoredReceiptMedia:
        """Decode, validate, and atomically store an original receipt image.

        Args:
            content: Complete untrusted image payload.

        Returns:
            Safe decoded facts and the content digest.

        Raises:
            ReceiptMediaError: If bytes, format, dimensions, frame count,
                decoding, archive paths, or existing content are unsafe.
        """

        # Every rejection this method can produce is a refusal to archive
        # untrusted bytes, so they are recorded together here rather than at
        # each of the twenty checks. The reason text is a fixed message from
        # this module; the image bytes and the archive path never appear.
        try:
            return self._store(content)
        except ReceiptMediaError as error:
            _LOGGER.warning(
                "receipt_media.rejected",
                reason=str(error),
                content_bytes=len(content),
            )
            raise

    def _store(self, content: bytes) -> StoredReceiptMedia:
        """Perform the validated atomic store this class exposes.

        Args:
            content: Complete untrusted image payload.

        Returns:
            Safe decoded facts and the content digest.

        Raises:
            ReceiptMediaError: If bytes, format, dimensions, frame count,
                decoding, archive paths, or existing content are unsafe.
        """

        media_type, width, height = self._validate_image(content)
        digest = hashlib.sha256(content).hexdigest()
        target = self._target(digest, create_shard=True)
        if target.exists() or target.is_symlink():
            existing = self._read_target(target, digest)
            if existing != content:
                raise ReceiptMediaError("receipt digest path contains other content")
            # Re-storing identical content is the ordinary result of a retry.
            _LOGGER.debug("receipt_media.already_archived", content_sha256=digest)
            return StoredReceiptMedia(digest, media_type, len(content), width, height)

        file_descriptor: int | None = None
        temporary: Path | None = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".receipt-",
                dir=target.parent,
            )
            temporary = Path(temporary_name)
            os.fchmod(file_descriptor, 0o600)
            handle = os.fdopen(file_descriptor, "wb")
            file_descriptor = None
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                existing = self._read_target(target, digest)
                if existing != content:
                    raise ReceiptMediaError(
                        "receipt digest path contains other content"
                    ) from None
            self._sync_directory(target.parent)
        except ReceiptMediaError:
            raise
        except OSError as error:
            raise ReceiptMediaError(
                "receipt media cannot be stored atomically"
            ) from error
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        _LOGGER.info(
            "receipt_media.archived",
            content_sha256=digest,
            media_type=media_type,
            content_bytes=len(content),
            width=width,
            height=height,
        )
        return StoredReceiptMedia(digest, media_type, len(content), width, height)

    def read(self, content_sha256: str) -> bytes:
        """Read one original by digest and detect filesystem tampering.

        Args:
            content_sha256: Lowercase content key, never a path.

        Returns:
            Original validated bytes.

        Raises:
            ReceiptMediaError: If the key is malformed, missing, symlinked,
                oversized, or no longer matches its digest.
        """

        target = self._target(content_sha256, create_shard=False)
        try:
            return self._read_target(target, content_sha256)
        except ReceiptMediaError as error:
            # A digest mismatch here means the archive was altered underneath
            # the application, which is worth an error rather than a warning.
            _LOGGER.error(
                "receipt_media.unreadable",
                reason=str(error),
                content_sha256=content_sha256,
            )
            raise

    @staticmethod
    def _validate_image(content: bytes) -> tuple[str, int, int]:
        """Fully decode supported single-frame content under explicit limits."""

        if not content:
            raise ReceiptMediaError("receipt image cannot be empty")
        if len(content) > MAX_RECEIPT_BYTES:
            raise ReceiptMediaError("receipt image exceeds the byte limit")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    media_type = _FORMAT_MEDIA_TYPES.get(image.format or "")
                    if media_type is None:
                        raise ReceiptMediaError("unsupported decoded receipt format")
                    width, height = image.size
                    if width <= 0 or height <= 0:
                        raise ReceiptMediaError("receipt dimensions must be positive")
                    if width > MAX_RECEIPT_DIMENSION or height > MAX_RECEIPT_DIMENSION:
                        raise ReceiptMediaError("receipt dimension exceeds the limit")
                    if width * height > MAX_RECEIPT_PIXELS:
                        raise ReceiptMediaError("receipt image exceeds the pixel limit")
                    if getattr(image, "n_frames", 1) != 1:
                        raise ReceiptMediaError(
                            "animated receipt images are unsupported"
                        )
                    image.verify()
                with Image.open(BytesIO(content)) as decoded:
                    decoded.load()
        except ReceiptMediaError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ) as error:
            raise ReceiptMediaError("receipt image cannot be safely decoded") from error
        return media_type, width, height

    def _target(self, digest: str, *, create_shard: bool) -> Path:
        """Resolve only a validated digest to an application-controlled path."""

        if _SHA256.fullmatch(digest) is None:
            raise ReceiptMediaError("receipt media key must be lowercase SHA-256")
        shard = self._root / digest[:2]
        if create_shard:
            try:
                shard.mkdir(mode=0o700, exist_ok=True)
            except OSError as error:
                raise ReceiptMediaError(
                    "receipt archive shard must be a directory"
                ) from error
            if shard.is_symlink() or not shard.is_dir():
                raise ReceiptMediaError("receipt archive shard must be a directory")
            try:
                os.chmod(shard, 0o700)
            except OSError as error:
                raise ReceiptMediaError(
                    "receipt archive shard cannot be secured"
                ) from error
        elif shard.is_symlink():
            raise ReceiptMediaError("receipt archive shard cannot be a symlink")
        return shard / digest

    @staticmethod
    def _read_target(target: Path, expected_digest: str) -> bytes:
        """Read without following a final symlink and verify size plus digest."""

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(target, flags)
            with os.fdopen(file_descriptor, "rb") as handle:
                content = handle.read(MAX_RECEIPT_BYTES + 1)
        except OSError as error:
            raise ReceiptMediaError("receipt media does not exist safely") from error
        if len(content) > MAX_RECEIPT_BYTES:
            raise ReceiptMediaError("stored receipt exceeds the byte limit")
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise ReceiptMediaError("stored receipt content hash mismatch")
        return content

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        """Persist the newly linked archive entry before reporting success."""

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
