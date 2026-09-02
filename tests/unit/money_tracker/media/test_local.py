"""Tests for the real private content-addressed receipt archive."""

from __future__ import annotations

import hashlib
import stat
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from money_tracker.application import ReceiptMediaError
from money_tracker.media.local import (
    MAX_RECEIPT_BYTES,
    MAX_RECEIPT_DIMENSION,
    MAX_RECEIPT_PIXELS,
    LocalReceiptMediaStore,
)


def _image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (32, 24),
) -> bytes:
    """Encode a deterministic small image through the production decoder library."""

    output = BytesIO()
    Image.new("RGB", size, color=(230, 240, 245)).save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_store_decodes_supported_formats_and_uses_private_permissions(
    tmp_path: Path,
    image_format: str,
    media_type: str,
) -> None:
    """Detect actual format and retain bytes under mode-restricted hash paths."""

    root = tmp_path / "receipts"
    store = LocalReceiptMediaStore(root)
    content = _image_bytes(image_format)

    stored = store.store(content)

    digest = hashlib.sha256(content).hexdigest()
    target = root / digest[:2] / digest
    assert stored.content_sha256 == digest
    assert stored.media_type == media_type
    assert stored.byte_size == len(content)
    assert (stored.width, stored.height) == (32, 24)
    assert store.read(digest) == content
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_concurrent_identical_stores_create_one_file_without_temporary_leaks(
    tmp_path: Path,
) -> None:
    """Converge concurrent writers through an atomic no-overwrite link."""

    root = tmp_path / "receipts"
    store = LocalReceiptMediaStore(root)
    content = _image_bytes()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(store.store, (content,) * 16))

    assert len(set(results)) == 1
    digest = results[0].content_sha256
    assert store.read(digest) == content
    assert tuple((root / digest[:2]).iterdir()) == (root / digest[:2] / digest,)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty"),
        (b"not an image", "decoded"),
        (b"x" * (MAX_RECEIPT_BYTES + 1), "byte limit"),
    ],
)
def test_store_rejects_empty_invalid_and_oversized_payloads(
    tmp_path: Path,
    content: bytes,
    message: str,
) -> None:
    """Reject unsafe content before any authoritative archive file appears."""

    root = tmp_path / "receipts"
    store = LocalReceiptMediaStore(root)

    with pytest.raises(ReceiptMediaError, match=message):
        store.store(content)

    assert tuple(root.iterdir()) == ()


def test_store_rejects_unsupported_format_and_truncated_supported_image(
    tmp_path: Path,
) -> None:
    """Require one supported image to pass both structural and full decode checks."""

    store = LocalReceiptMediaStore(tmp_path / "receipts")
    gif = _image_bytes("GIF")
    truncated_png = _image_bytes()[:-12]

    with pytest.raises(ReceiptMediaError, match="unsupported"):
        store.store(gif)
    with pytest.raises(ReceiptMediaError, match="decoded"):
        store.store(truncated_png)


def test_store_rejects_dimension_limit_before_full_decode(tmp_path: Path) -> None:
    """Reject an extreme side length even when total pixels remain small."""

    store = LocalReceiptMediaStore(tmp_path / "receipts")
    content = _image_bytes(size=(MAX_RECEIPT_DIMENSION + 1, 1))

    with pytest.raises(ReceiptMediaError, match="dimension"):
        store.store(content)


def test_store_rejects_pixel_limit_before_full_decode(tmp_path: Path) -> None:
    """Reject a decompression-sized bitmap even when each side is allowed."""

    store = LocalReceiptMediaStore(tmp_path / "receipts")
    size = (7000, 6000)
    assert size[0] * size[1] > MAX_RECEIPT_PIXELS
    output = BytesIO()
    Image.new("1", size).save(output, format="PNG")

    with pytest.raises(ReceiptMediaError, match="pixel limit"):
        store.store(output.getvalue())


def test_store_rejects_animated_supported_image(tmp_path: Path) -> None:
    """Reject multi-frame WebP even though static WebP is supported."""

    store = LocalReceiptMediaStore(tmp_path / "receipts")
    output = BytesIO()
    first = Image.new("RGB", (8, 8), color="white")
    second = Image.new("RGB", (8, 8), color="black")
    first.save(
        output,
        format="WEBP",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )

    with pytest.raises(ReceiptMediaError, match="animated"):
        store.store(output.getvalue())


def test_read_rejects_path_input_missing_content_and_tampering(tmp_path: Path) -> None:
    """Accept only digest keys and detect a changed authoritative file."""

    root = tmp_path / "receipts"
    store = LocalReceiptMediaStore(root)

    with pytest.raises(ReceiptMediaError, match="SHA-256"):
        store.read("../../etc/passwd")
    with pytest.raises(ReceiptMediaError, match="does not exist"):
        store.read("f" * 64)

    stored = store.store(_image_bytes())
    target = root / stored.content_sha256[:2] / stored.content_sha256
    target.write_bytes(b"tampered")
    with pytest.raises(ReceiptMediaError, match="hash mismatch"):
        store.read(stored.content_sha256)


def test_store_and_read_reject_symlinked_archive_paths(tmp_path: Path) -> None:
    """Do not follow a configured root, digest shard, or final media symlink."""

    external = tmp_path / "external"
    external.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(external, target_is_directory=True)
    with pytest.raises(ReceiptMediaError, match="root cannot be a symlink"):
        LocalReceiptMediaStore(root_link)

    root = tmp_path / "receipts"
    store = LocalReceiptMediaStore(root)
    content = _image_bytes()
    digest = hashlib.sha256(content).hexdigest()
    shard = root / digest[:2]
    shard.symlink_to(external, target_is_directory=True)
    with pytest.raises(ReceiptMediaError, match="shard"):
        store.store(content)
    shard.unlink()

    shard.mkdir()
    target = shard / digest
    external_file = external / "payload"
    external_file.write_bytes(content)
    target.symlink_to(external_file)
    with pytest.raises(ReceiptMediaError, match="does not exist safely"):
        store.store(content)


def test_archive_root_rejects_regular_file(tmp_path: Path) -> None:
    """Fail closed when the configured archive root is not a directory."""

    root = tmp_path / "receipt-file"
    root.write_bytes(b"not a directory")

    with pytest.raises(ReceiptMediaError):
        LocalReceiptMediaStore(root)


def test_store_rejects_directory_at_final_digest_path(tmp_path: Path) -> None:
    """Return a typed safety error when a hash key is not a regular file."""

    root = tmp_path / "receipts"
    store = LocalReceiptMediaStore(root)
    content = _image_bytes()
    digest = hashlib.sha256(content).hexdigest()
    (root / digest[:2] / digest).mkdir(parents=True)

    with pytest.raises(ReceiptMediaError, match="does not exist safely"):
        store.store(content)
