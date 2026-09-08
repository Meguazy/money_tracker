"""Unit tests for executable documentation validation."""

from __future__ import annotations

from pathlib import Path

from money_tracker.development.docs_validation import validate_repository


def test_repository_documentation_passes_all_checks() -> None:
    """Validate the real canonical documentation and evaluation indexes."""

    repository_root = Path(__file__).resolve().parents[4]

    assert validate_repository(repository_root) == []


def test_missing_relative_link_has_an_actionable_diagnostic(tmp_path: Path) -> None:
    """Report the source document and missing target."""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("[Missing](not-here.md)\n", encoding="utf-8")
    eval_private = tmp_path / "evals" / "private"
    eval_private.mkdir(parents=True)
    (eval_private / "README.md").write_text("# Private\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "\n".join(
            (
                "/artifacts/evals/",
                "/artifacts/sweeps/",
                "/evals/private/*",
                "!/evals/private/README.md",
            )
        ),
        encoding="utf-8",
    )

    issues = validate_repository(tmp_path)

    assert len(issues) == 1
    assert "guide.md" in issues[0]
    assert "not-here.md" in issues[0]


def test_unbalanced_fence_is_rejected(tmp_path: Path) -> None:
    """Fail a Markdown document with an unfinished fenced code block."""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\n```text\nopen\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "/artifacts/evals/\n/artifacts/sweeps/\n"
        "/evals/private/*\n!/evals/private/README.md\n",
        encoding="utf-8",
    )

    assert any(
        "unbalanced fenced code block" in issue
        for issue in validate_repository(tmp_path)
    )


def test_invalid_model_catalog_yaml_is_rejected(tmp_path: Path) -> None:
    """Parse experimental model catalogs as canonical evaluation input."""

    models = tmp_path / "evals" / "models"
    models.mkdir(parents=True)
    (models / "candidates.yaml").write_text("profiles: [broken\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "/artifacts/evals/\n/artifacts/sweeps/\n"
        "/evals/private/*\n!/evals/private/README.md\n",
        encoding="utf-8",
    )

    issues = validate_repository(tmp_path)

    assert any("candidates.yaml: invalid YAML" in issue for issue in issues)
