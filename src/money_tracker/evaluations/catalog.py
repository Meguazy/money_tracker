"""Read the local model candidate catalog as executable configuration.

`evals/models/local-candidates.yaml` pins the exact weights a candidate is,
including the SHA-256 of every GGUF. Until now nothing read it, so the file
documented an intention that no run could check: the model under test was
whatever the local server happened to have loaded, named only by an alias that
never changes.

This module makes the catalog executable in two ways. It lets a run declare the
profile it means to measure and refuse to proceed when a different one is
loaded, and it names the model behind a published score by its catalog profile
rather than by a filename, so scores for different candidates can be kept and
compared instead of overwriting one another.

The catalog stays the single source of truth for what a candidate is. This
module never downloads, installs, or selects weights; it reads what is declared
and compares it with what is present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CATALOG_RELATIVE_PATH = Path("evals") / "models" / "local-candidates.yaml"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(ValueError):
    """The candidate catalog is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """One evaluable local model candidate declared by the catalog.

    Attributes:
        id: Stable catalog profile identifier, such as `text-qwen3-14b`.
        modality: Declared modality, `text` or `vision`.
        artifact: Artifact key the profile refers to.
        path: Absolute local path the weights are expected at.
        sha256: Lowercase hex SHA-256 the weights must hash to.
        quantization: Declared quantization label.
        projector_path: Absolute local path of the multimodal projector, when
            the artifact declares one.
        projector_sha256: Lowercase hex SHA-256 of that projector.
    """

    id: str
    modality: str
    artifact: str
    path: Path
    sha256: str
    quantization: str | None
    projector_path: Path | None
    projector_sha256: str | None

    @property
    def filename(self) -> str:
        """Return the weights filename the inference server should report."""

        return self.path.name


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Every candidate profile declared for the documented local workstation.

    Attributes:
        profiles: Profiles by catalog identifier, in declaration order.
        model_root: Absolute directory the declared local paths resolve under.
    """

    profiles: dict[str, ModelProfile]
    model_root: Path

    def profile(self, identifier: str) -> ModelProfile:
        """Return one declared profile.

        Args:
            identifier: Catalog profile identifier.

        Returns:
            The declared profile.

        Raises:
            CatalogError: If no profile carries that identifier. The message
                lists the identifiers that exist, because a typo and an
                unavailable candidate are otherwise indistinguishable.
        """

        try:
            return self.profiles[identifier]
        except KeyError:
            known = ", ".join(sorted(self.profiles)) or "none"
            raise CatalogError(
                f"unknown model profile {identifier!r}; the catalog declares: {known}"
            ) from None

    def by_sha256(
        self, digest: str | None, *, modality: str | None = None
    ) -> ModelProfile | None:
        """Identify a profile by the exact weights a run measured.

        One artifact may serve several profiles: the same multimodal weights are
        declared once for text and once for vision. Pass the modality under test
        to name the right one; without it the first declared profile wins, which
        is deterministic but says nothing about how the weights were used.

        Args:
            digest: Hash recorded by a run, with or without an algorithm prefix.
            modality: Restrict the answer to profiles declared for this modality.

        Returns:
            The matching profile, or None when the weights are not catalogued.
            An uncatalogued model is a legitimate exploratory run, not an error.
        """

        if not digest:
            return None
        normalized = digest.split(":", 1)[-1].strip().lower()
        for profile in self.profiles.values():
            if profile.sha256 != normalized:
                continue
            if modality is None or profile.modality == modality:
                return profile
        return None

    def by_filename(
        self, filename: str | None, *, modality: str | None = None
    ) -> ModelProfile | None:
        """Identify a profile by the weights filename a run loaded.

        This is weaker than :meth:`by_sha256` and deliberately kept separate: a
        filename names the candidate but not the revision, so two downloads of
        the same name are indistinguishable here. Use it only where a run could
        not record a hash at all.

        Args:
            filename: Weights filename recorded by a run.
            modality: Restrict the answer to profiles declared for this modality.

        Returns:
            The matching profile, or None when the filename is not catalogued.
        """

        if not filename:
            return None
        name = Path(filename).name
        for profile in self.profiles.values():
            if profile.filename != name:
                continue
            if modality is None or profile.modality == modality:
                return profile
        return None

    def identifiers(self) -> list[str]:
        """Return every declared profile identifier in declaration order."""

        return list(self.profiles)


def _require_mapping(value: object, what: str) -> dict[str, object]:
    """Narrow one decoded YAML node to a mapping.

    Args:
        value: Decoded YAML node.
        what: Node description used in the error message.

    Returns:
        The mapping.

    Raises:
        CatalogError: If the node is not a mapping.
    """

    if not isinstance(value, dict):
        raise CatalogError(f"{what} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _digest(value: object, what: str) -> str:
    """Validate one declared SHA-256.

    Args:
        value: Declared digest.
        what: Node description used in the error message.

    Returns:
        The lowercase hex digest without an algorithm prefix.

    Raises:
        CatalogError: If the digest is absent or not 64 hex characters. A
            catalog that cannot pin weights cannot verify them either.
    """

    digest = str(value or "").split(":", 1)[-1].strip().lower()
    if not _HEX_SHA256.match(digest):
        raise CatalogError(f"{what} must declare a hex SHA-256")
    return digest


def load_catalog(catalog_path: Path) -> ModelCatalog:
    """Load and validate the declared candidate catalog.

    Args:
        catalog_path: Committed catalog YAML.

    Returns:
        Every declared profile with its local paths resolved absolutely.

    Raises:
        CatalogError: If the file is missing or malformed, if a profile refers
            to an artifact the catalog does not declare, or if an artifact does
            not pin its weights by hash.
    """

    if not catalog_path.is_file():
        raise CatalogError(f"model catalog not found: {catalog_path}")
    try:
        decoded = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise CatalogError(f"{catalog_path.name}: invalid YAML: {error}") from error

    document = _require_mapping(decoded, catalog_path.name)
    model_root = Path(str(document.get("model_root", ""))).expanduser()
    artifacts = _require_mapping(document.get("artifacts"), "artifacts")
    declared = document.get("profiles")
    if not isinstance(declared, list):
        raise CatalogError("profiles must be a list")

    profiles: dict[str, ModelProfile] = {}
    for entry in declared:
        profile = _require_mapping(entry, "profile")
        identifier = str(profile.get("id", "")).strip()
        artifact_key = str(profile.get("artifact", "")).strip()
        if not identifier:
            raise CatalogError("every profile must declare an id")
        if identifier in profiles:
            raise CatalogError(f"profile {identifier!r} is declared twice")
        if artifact_key not in artifacts:
            raise CatalogError(
                f"profile {identifier!r} refers to undeclared artifact {artifact_key!r}"
            )
        artifact = _require_mapping(artifacts[artifact_key], f"artifact {artifact_key}")
        projector = artifact.get("projector")
        projector_node = (
            _require_mapping(projector, f"artifact {artifact_key} projector")
            if projector is not None
            else None
        )
        quantization = artifact.get("quantization")
        profiles[identifier] = ModelProfile(
            id=identifier,
            modality=str(profile.get("modality", "")).strip(),
            artifact=artifact_key,
            path=model_root / str(artifact.get("local_path", "")),
            sha256=_digest(artifact.get("sha256"), f"artifact {artifact_key}"),
            quantization=str(quantization) if quantization is not None else None,
            projector_path=(
                model_root / str(projector_node.get("local_path", ""))
                if projector_node is not None
                else None
            ),
            projector_sha256=(
                _digest(
                    projector_node.get("sha256"),
                    f"artifact {artifact_key} projector",
                )
                if projector_node is not None
                else None
            ),
        )

    if not profiles:
        raise CatalogError("the catalog declares no profiles")
    return ModelCatalog(profiles=profiles, model_root=model_root)


def profile_mismatches(
    profile: ModelProfile,
    *,
    configured_path: Path | None,
    server_filename: str | None,
) -> list[str]:
    """Report why the running configuration is not the declared candidate.

    This is the guard against the failure that invalidates a comparison without
    announcing itself: editing the configured weights and forgetting to restart
    the inference server, so a run attributes one candidate's answers to
    another. Both halves are checked, because either alone can be right while
    the other is wrong.

    Weights are compared by name rather than by hash. The hash is recorded by
    the run itself and decides which profile the score is published under, so a
    file whose contents drifted from the catalog is caught there; this check
    exists to fail before a run rather than after one.

    Args:
        profile: Candidate the run declared it would measure.
        configured_path: Local weights path the run will load and hash.
        server_filename: Weights filename the inference server reports loading.

    Returns:
        One human-readable problem per mismatch, empty when the configuration
        and the server both agree with the declared candidate.
    """

    problems: list[str] = []
    if configured_path is None:
        problems.append("MODEL_PATH is not set, so the run cannot pin its weights")
    elif configured_path.name != profile.filename:
        problems.append(
            f"MODEL_PATH names {configured_path.name}, "
            f"but {profile.id} declares {profile.filename}"
        )
    if server_filename is None:
        problems.append(
            "the inference server did not report a loaded model, so the run "
            "cannot confirm which weights would answer"
        )
    elif server_filename != profile.filename:
        problems.append(
            f"the inference server has {server_filename} loaded, "
            f"but {profile.id} declares {profile.filename}; restart it"
        )
    return problems


__all__ = [
    "CATALOG_RELATIVE_PATH",
    "CatalogError",
    "ModelCatalog",
    "ModelProfile",
    "load_catalog",
    "profile_mismatches",
]
