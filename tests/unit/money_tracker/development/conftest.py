"""Shared isolation for development-policy tests that drive real Git."""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture(autouse=True)
def _isolate_from_the_calling_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the environment that ties Git to the repository running pytest.

    Git hooks run with repository-local variables such as `GIT_DIR` exported;
    in a linked worktree that points at the shared repository. A test that
    builds its own repository in `tmp_path` would otherwise commit into, create
    branches in, and reconfigure the repository whose hook is running.
    """

    names = subprocess.run(
        ("git", "rev-parse", "--local-env-vars"),
        check=True,
        text=True,
        capture_output=True,
    ).stdout.split()
    for name in names:
        monkeypatch.delenv(name, raising=False)
