"""Production clock and identifier implementations for application services."""

from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    """Return wall-clock UTC instants for authoritative mutation metadata."""

    def now(self) -> datetime:
        """Return the current aware UTC instant."""

        return datetime.now(UTC)


class UUID4Generator:
    """Generate random UUID identifiers at the application boundary."""

    def new(self) -> UUID:
        """Return a new UUID4 value."""

        return uuid4()
