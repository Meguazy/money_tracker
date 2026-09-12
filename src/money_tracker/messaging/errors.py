"""Failure classes the durable messaging port may raise.

They live beside the port rather than beside the PostgreSQL adapter because
they are part of what `MessagingRepository` promises. A caller that depends
only on the port must be able to name the failures it has to handle without
importing a storage implementation.
"""

from __future__ import annotations


class MessagingConflictError(RuntimeError):
    """Report a stable identity reused with different durable content."""


class MessagingLeaseError(RuntimeError):
    """Report an expired, stale, or otherwise invalid worker lease."""


__all__ = ["MessagingConflictError", "MessagingLeaseError"]
