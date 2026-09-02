"""Typed failures raised by deterministic finance-domain policies."""


class DomainError(ValueError):
    """Base class for invalid finance-domain state or transitions."""


class ReconciliationError(DomainError):
    """Raised when monetary allocations do not reconcile with their aggregate."""


class InvalidTransitionError(DomainError):
    """Raised when an aggregate lifecycle transition is not permitted."""


class TaxonomyError(DomainError):
    """Raised when a category operation violates taxonomy governance."""


class WalletError(DomainError):
    """Raised when a wallet entry or balance operation is invalid."""
