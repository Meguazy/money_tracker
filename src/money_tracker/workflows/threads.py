"""Stable identities of persistent LangGraph workflow threads.

A checkpoint thread identifier is a contract between two components with
different lifecycles. The dispatcher writes checkpoints under it, and runtime
retention later decides from it alone whether a thread can still be resumed.
Both therefore build and read identifiers here, so a new graph cannot start
writing threads that retention silently misclassifies.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ThreadNamespace(StrEnum):
    """Graph families that persist checkpoints, keyed by what they resume on.

    Attributes:
        ROUTER: Root routing, keyed by the inbound source reference.
        EXPENSE: Expense or refund specialist, keyed by the source reference.
        READ: Read-only question or advice, keyed by the source reference.
        MOVEMENT_CHANGE: Correction or undo, keyed by the source reference.
        CATEGORY_REVIEW: Owner category review, keyed by the proposal id. It
            is the only family that pauses, and it may wait indefinitely.
    """

    ROUTER = "router"
    EXPENSE = "expense"
    READ = "read"
    MOVEMENT_CHANGE = "movement-change"
    CATEGORY_REVIEW = "category-review"


SOURCE_KEYED_NAMESPACES: Final = frozenset(
    {
        ThreadNamespace.ROUTER,
        ThreadNamespace.EXPENSE,
        ThreadNamespace.READ,
        ThreadNamespace.MOVEMENT_CHANGE,
    }
)
"""Families that run to completion within one inbound work unit.

Their thread is resumed only by a retry of the same source reference, so once
that work unit is terminal the thread can never be continued.
"""

THREAD_SEPARATOR: Final = ":"
"""Separator between the namespace and the reference it is keyed by."""


def workflow_thread_id(namespace: ThreadNamespace, reference: str) -> str:
    """Build the collision-free identifier of one persistent workflow thread.

    Args:
        namespace: Graph family writing the checkpoints.
        reference: Source reference or proposal id the thread is keyed by. It
            may itself contain the separator; only the first one splits.

    Returns:
        `<namespace>:<reference>`.

    Raises:
        ValueError: If the reference is blank.
    """

    if not reference.strip():
        raise ValueError("workflow thread reference cannot be blank")
    return f"{namespace.value}{THREAD_SEPARATOR}{reference}"


__all__ = [
    "SOURCE_KEYED_NAMESPACES",
    "THREAD_SEPARATOR",
    "ThreadNamespace",
    "workflow_thread_id",
]
