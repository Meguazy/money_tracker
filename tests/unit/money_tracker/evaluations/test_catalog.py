"""Tests for the executable local model candidate catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from money_tracker.evaluations.catalog import (
    CATALOG_RELATIVE_PATH,
    CatalogError,
    ModelCatalog,
    load_catalog,
    profile_mismatches,
)

COMMITTED = Path(CATALOG_RELATIVE_PATH)
QWEN_SHA = "e7c9aba1129ca2936be9eca01419d9f86af40e08caa01230d5574b34d08e3e31"
MINISTRAL = "Ministral-3-14B-Instruct-2512-Q5_K_M.gguf"


def _catalog(tmp_path: Path, body: str) -> ModelCatalog:
    """Load one catalog fixture written to a temporary file."""

    path = tmp_path / "candidates.yaml"
    path.write_text(body, encoding="utf-8")
    return load_catalog(path)


def _valid(**overrides: str) -> str:
    """Render a minimal valid catalog with optional broken fields."""

    fields = {
        "model_root": "~/models",
        "local_path": "text/one/one.gguf",
        "sha256": "a" * 64,
        "artifact": "one",
        "id": "text-one",
    }
    fields.update(overrides)
    return f"""
model_root: {fields["model_root"]}
artifacts:
  one:
    local_path: {fields["local_path"]}
    sha256: {fields["sha256"]}
    quantization: Q5_K_M
profiles:
  - id: {fields["id"]}
    modality: text
    artifact: {fields["artifact"]}
"""


def test_the_committed_catalog_pins_every_declared_candidate() -> None:
    """Keep the file that names the candidates readable as configuration."""

    catalog = load_catalog(COMMITTED)

    assert "text-qwen3-14b" in catalog.identifiers()
    for profile in catalog.profiles.values():
        assert profile.path.is_absolute()
        assert profile.path.is_relative_to(catalog.model_root)
        assert len(profile.sha256) == 64
        assert profile.sha256 == profile.sha256.lower()
        assert profile.modality in {"text", "vision"}
        if profile.modality == "vision":
            assert profile.projector_path is not None
            assert profile.projector_sha256 is not None


def test_weights_shared_by_two_profiles_are_told_apart_by_modality() -> None:
    """Answer which profile the weights served rather than guessing one."""

    catalog = load_catalog(COMMITTED)

    text = catalog.by_sha256(f"sha256:{QWEN_SHA}", modality="text")
    shared_text = catalog.by_filename(MINISTRAL, modality="text")
    shared_vision = catalog.by_filename(MINISTRAL, modality="vision")

    assert text is not None and text.id == "text-qwen3-14b"
    assert catalog.by_sha256(QWEN_SHA) == text
    assert shared_text is not None and shared_text.id == "text-ministral3-14b"
    assert shared_vision is not None and shared_vision.id == "vision-ministral3-14b"
    assert shared_text.sha256 == shared_vision.sha256
    assert catalog.by_sha256(QWEN_SHA, modality="vision") is None
    assert catalog.by_sha256(None) is None
    assert catalog.by_filename(None) is None
    assert catalog.by_filename("not-a-candidate.gguf") is None


def test_an_unknown_profile_names_the_ones_that_exist(tmp_path: Path) -> None:
    """Separate a typo from a candidate the catalog never declared."""

    catalog = _catalog(tmp_path, _valid())

    with pytest.raises(CatalogError, match="the catalog declares: text-one"):
        catalog.profile("text-two")


def test_a_profile_may_not_refer_to_undeclared_weights(tmp_path: Path) -> None:
    """Refuse a candidate whose weights nothing in the catalog describes."""

    with pytest.raises(CatalogError, match="undeclared artifact 'two'"):
        _catalog(tmp_path, _valid(artifact="two"))


def test_weights_that_are_not_pinned_by_hash_are_refused(tmp_path: Path) -> None:
    """Keep the catalog unable to declare a candidate it cannot verify."""

    with pytest.raises(CatalogError, match="must declare a hex SHA-256"):
        _catalog(tmp_path, _valid(sha256="not-a-digest"))


def test_a_malformed_or_missing_catalog_is_refused(tmp_path: Path) -> None:
    """Name the file rather than failing later against an empty profile set."""

    missing = tmp_path / "absent.yaml"
    with pytest.raises(CatalogError, match="model catalog not found"):
        load_catalog(missing)

    broken = tmp_path / "candidates.yaml"
    broken.write_text("profiles: [oops\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="invalid YAML"):
        load_catalog(broken)

    empty = tmp_path / "empty.yaml"
    empty.write_text("model_root: ~/models\nartifacts: {}\nprofiles: []\n")
    with pytest.raises(CatalogError, match="declares no profiles"):
        load_catalog(empty)


def test_a_declared_path_resolves_under_the_declared_root(tmp_path: Path) -> None:
    """Resolve a home-relative catalog root without depending on the shell."""

    catalog = _catalog(tmp_path, _valid())
    profile = catalog.profile("text-one")

    assert profile.path == Path.home() / "models" / "text" / "one" / "one.gguf"
    assert profile.filename == "one.gguf"
    assert profile.quantization == "Q5_K_M"
    assert profile.projector_path is None


def test_a_run_measuring_other_weights_is_told_which_half_is_wrong(
    tmp_path: Path,
) -> None:
    """Separate a stale server from a mis-edited environment."""

    profile = _catalog(tmp_path, _valid()).profile("text-one")

    assert not profile_mismatches(
        profile,
        configured_path=Path("/models/one.gguf"),
        server_filename="one.gguf",
    )

    stale_server = profile_mismatches(
        profile,
        configured_path=Path("/models/one.gguf"),
        server_filename="other.gguf",
    )
    assert len(stale_server) == 1
    assert "restart it" in stale_server[0]

    wrong_environment = profile_mismatches(
        profile,
        configured_path=Path("/models/other.gguf"),
        server_filename="one.gguf",
    )
    assert len(wrong_environment) == 1
    assert "MODEL_PATH names other.gguf" in wrong_environment[0]

    unusable = profile_mismatches(profile, configured_path=None, server_filename=None)
    assert len(unusable) == 2
