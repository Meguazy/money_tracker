"""Typed application failures independent of transport and database libraries."""


class ApplicationError(RuntimeError):
    """Base class for finance use-case failures."""


class AggregateNotFoundError(ApplicationError):
    """Raised when a stable aggregate identifier is outside the profile scope."""


class IdempotencyConflictError(ApplicationError):
    """Raised when one command key is reused for different request content."""


class OptimisticConcurrencyError(ApplicationError):
    """Raised when a mutation targets an aggregate version that is no longer current."""


class ReceiptMediaError(ApplicationError):
    """Raised when receipt content or its private archive violates safety rules."""
