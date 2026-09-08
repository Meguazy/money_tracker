"""Versioned instruction catalogs for the workflow model boundaries.

Nothing here is promoted. AD-012 and the experiment process require a prompt to
pass its evaluation gates and be recorded in the decision log before a runtime
adopts it, so every version identifier carries a `candidate-` prefix and the
decision log states that no V1 production prompt exists.

The texts live in `prompt_data/<boundary>.json` rather than in Python, one file
per boundary, each holding every version of that boundary's instructions with
the note that says why the version exists. Prompts are data that changes far
more often than the code reading it, and a boundary accumulates versions: an
evaluation compares them, so the ones it compares must all still be there,
readable side by side rather than buried in string literals.

Instruction language is itself an open evaluation variable. These candidates are
written in English, matching the repository's technical language, while the
input they interpret and the product surface remain Italian.

Two vocabularies stay derived rather than retyped. The routable intents come
from the taxonomy in `intents` and the read tools from the registry in `tools`,
so an added or renamed route cannot silently disagree with the text the model is
given: a catalog whose vocabulary no longer matches is refused at load time
instead of quietly offering a route that does not exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

from money_tracker.workflows.intents import (
    MODEL_ROUTABLE_INTENTS,
    WorkflowIntent,
)
from money_tracker.workflows.tools import ReadTool, registry_for

_DATA_ROOT: Final = Path(__file__).parent / "prompt_data"
"""Directory holding one JSON catalog per boundary."""

ROUTER: Final = "router"
EXTRACTION: Final = "extraction"
CATEGORY_FIT: Final = "category_fit"
TOOL_SELECTION: Final = "tool_selection"

BOUNDARIES: Final = (ROUTER, EXTRACTION, CATEGORY_FIT, TOOL_SELECTION)
"""Every boundary that owns an instruction catalog."""


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """One boundary instruction text and the identity a run records.

    Attributes:
        boundary: Which boundary the text instructs.
        id: Version identifier recorded by an evaluation run.
        short_name: Trailing `vN` of the identifier, which is how a run selects
            a version on the command line.
        status: Either `candidate` for the boundary's current default or
            `superseded` for one kept so its recorded runs stay readable.
        note: Why this version exists and what its run measured.
        text: The rendered instruction text, vocabularies already substituted.
    """

    boundary: str
    id: str
    short_name: str
    status: str
    note: str
    text: str


class PromptCatalogError(RuntimeError):
    """Raised when a catalog is missing, malformed, or out of date."""


def _short_name(identifier: str) -> str:
    """Return the trailing version segment used to select a version.

    Args:
        identifier: Full version identifier such as `candidate-router-it-v4`.

    Returns:
        The trailing segment, such as `v4`.

    Raises:
        PromptCatalogError: If the identifier carries no trailing segment.
    """

    tail = identifier.rsplit("-", 1)[-1]
    if not tail.startswith("v") or not tail[1:].isdigit():
        raise PromptCatalogError(f"{identifier} has no trailing version segment")
    return tail


def _render_intent_lines(guidance: dict[str, str]) -> str:
    """Render the routable vocabulary in a stable deterministic order.

    Args:
        guidance: One line of guidance per routable intent value.

    Returns:
        A newline-separated list of intents and their guidance.

    Raises:
        PromptCatalogError: If the guidance does not cover exactly the routable
            intents, which would offer the model a route that does not exist or
            hide one that does.
    """

    expected = {intent.value for intent in MODEL_ROUTABLE_INTENTS}
    if set(guidance) != expected:
        missing = sorted(expected - set(guidance))
        extra = sorted(set(guidance) - expected)
        raise PromptCatalogError(
            f"router guidance mismatch: missing={missing} unexpected={extra}"
        )
    return "\n".join(
        f"- {intent.value}: {guidance[intent.value]}"
        for intent in sorted(MODEL_ROUTABLE_INTENTS)
    )


@cache
def _catalog(boundary: str) -> dict[str, object]:
    """Load and validate one boundary's catalog.

    Args:
        boundary: Boundary name.

    Returns:
        The decoded catalog.

    Raises:
        PromptCatalogError: If the catalog is absent or malformed.
    """

    if boundary not in BOUNDARIES:
        raise PromptCatalogError(f"unknown prompt boundary {boundary}")
    path = _DATA_ROOT / f"{boundary}.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PromptCatalogError(f"{boundary} catalog is unreadable") from error
    if not isinstance(decoded, dict) or not isinstance(decoded.get("versions"), list):
        raise PromptCatalogError(f"{boundary} catalog has no versions")
    return decoded


@cache
def prompt_versions(boundary: str) -> tuple[PromptVersion, ...]:
    """Return every version of one boundary's instructions, oldest first.

    Args:
        boundary: Boundary name.

    Returns:
        The boundary's versions.

    Raises:
        PromptCatalogError: If the catalog is malformed or repeats a version.
    """

    catalog = _catalog(boundary)
    raw = catalog["versions"]
    if not isinstance(raw, list):
        raise PromptCatalogError(f"{boundary} catalog has no versions")
    versions: list[PromptVersion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise PromptCatalogError(f"{boundary} catalog holds a non-object version")
        identifier = str(entry["id"])
        text = str(entry["text"])
        guidance = entry.get("intent_guidance")
        if isinstance(guidance, dict):
            text = text.replace(
                "{intent_lines}",
                _render_intent_lines({str(k): str(v) for k, v in guidance.items()}),
            )
        versions.append(
            PromptVersion(
                boundary=boundary,
                id=identifier,
                short_name=_short_name(identifier),
                status=str(entry.get("status", "superseded")),
                note=str(entry.get("note", "")),
                text=text,
            )
        )
    if len({version.id for version in versions}) != len(versions):
        raise PromptCatalogError(f"{boundary} catalog repeats a version identifier")
    if len({version.short_name for version in versions}) != len(versions):
        raise PromptCatalogError(f"{boundary} catalog repeats a short name")
    return tuple(versions)


def prompt(boundary: str, version: str) -> PromptVersion:
    """Return one version of a boundary's instructions.

    Args:
        boundary: Boundary name.
        version: Either the full identifier or its trailing `vN` segment.

    Returns:
        The requested version.

    Raises:
        PromptCatalogError: If the boundary has no such version.
    """

    for candidate in prompt_versions(boundary):
        if version in (candidate.id, candidate.short_name):
            return candidate
    known = ", ".join(candidate.short_name for candidate in prompt_versions(boundary))
    raise PromptCatalogError(f"{boundary} has no version {version}; known: {known}")


def default_prompt(boundary: str) -> PromptVersion:
    """Return the version a run uses when none is named.

    Args:
        boundary: Boundary name.

    Returns:
        The catalog's declared default version.

    Raises:
        PromptCatalogError: If the catalog declares no usable default.
    """

    declared = _catalog(boundary).get("default_version")
    if not isinstance(declared, str):
        raise PromptCatalogError(f"{boundary} catalog declares no default version")
    return prompt(boundary, declared)


def render_active_categories(names: tuple[str, ...]) -> str:
    """Render the owner's active category list for the category-fit boundary.

    The list is built from the profile's taxonomy at call time rather than
    written into the instructions, so a model can only name a category the
    owner actually has.

    Args:
        names: Active category display names in taxonomy order.

    Returns:
        A newline-separated list to append to the instructions.

    Raises:
        ValueError: If no active category was supplied.
    """

    if not names:
        raise ValueError("category fit requires at least one active category")
    listed = "\n".join(f"- {name}" for name in names)
    return f"\nActive categories:\n{listed}\n"


def render_active_category_tree(
    nodes: tuple[tuple[str, str, str | None], ...],
) -> str:
    """Render the owner's active taxonomy as the hierarchy it actually is.

    `render_active_categories` renders a flat list of names, which asks a model
    to choose from a hierarchy it cannot see. The measured consequence was
    exactly that: twelve of thirty-one categorization errors were a heading
    chosen where the label was one of its own specific entries. Each node's
    inclusion guidance is written into the taxonomy and was likewise never
    shown, so it is rendered here too.

    Args:
        nodes: Active categories as `(name, description, parent name)`, in
            taxonomy order. A node with no parent is a heading.

    Returns:
        A rendered tree to append to the instructions.

    Raises:
        ValueError: If no active category was supplied.
    """

    if not nodes:
        raise ValueError("category fit requires at least one active category")
    lines = [
        f"{'  ' if parent else ''}- {name}: {description}"
        for name, description, parent in nodes
    ]
    listed = "\n".join(lines)
    return f"\nActive categories:\n{listed}\n"


def render_selection_categories(names: tuple[str, ...]) -> str:
    """Render the categories a read tool may be asked about.

    The selection boundary asks for a category by name and then resolves it
    against the owner's taxonomy, but v1 and v2 never showed the model what
    that taxonomy contains. Six of twenty errors in the v2 run were a category
    named in the owner's words that no active category matches, and three more
    were a payment method offered as a category. Neither is a judgment the
    model could have made from what it was given.

    Args:
        names: Active category display names, in taxonomy order.

    Returns:
        A rendered list to append to the instructions, or an empty string when
        no taxonomy was supplied.
    """

    if not names:
        return ""
    listed = "\n".join(f"- {name}" for name in names)
    return f"\nCategories you may name:\n{listed}\n"


def render_tool_vocabulary(
    intent: WorkflowIntent, *, version: str | None = None
) -> str:
    """Render the tools one route may call, for its instruction text.

    Derived from the registry rather than retyped, so a model is never offered
    a tool deterministic acceptance would refuse.

    Args:
        intent: Route whose registry is being offered.
        version: Instruction identity selecting versioned tool descriptions.

    Returns:
        A newline-separated list to append to the instructions.

    Raises:
        DomainError: If the route owns no registry.
        PromptCatalogError: If the catalog does not describe every read tool.
    """

    catalog = _catalog(TOOL_SELECTION)
    guidance = catalog.get("tool_guidance")
    entries = catalog["versions"]
    if not isinstance(entries, list):
        raise PromptCatalogError("tool catalog has no versions")
    for entry in entries:
        if isinstance(entry, dict) and entry["id"] == version:
            guidance = entry.get("tool_guidance", guidance)
            break
    if not isinstance(guidance, dict) or set(guidance) != {
        tool.value for tool in ReadTool
    }:
        raise PromptCatalogError("tool guidance does not cover the read registry")
    listed = "\n".join(
        f"- {tool.value}: {guidance[tool.value]}"
        for tool in sorted(registry_for(intent))
    )
    return f"\nAvailable tools:\n{listed}\n"


__all__ = [
    "BOUNDARIES",
    "CATEGORY_FIT",
    "EXTRACTION",
    "ROUTER",
    "TOOL_SELECTION",
    "PromptCatalogError",
    "PromptVersion",
    "default_prompt",
    "prompt",
    "prompt_versions",
    "render_active_categories",
    "render_active_category_tree",
    "render_selection_categories",
    "render_tool_vocabulary",
]
