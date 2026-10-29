"""The contract every per-company source implements, and the errors it may raise."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from wl.config import Company


class ProviderError(RuntimeError):
    """A source answered, but with an error instead of data."""


class MissingIdentifierError(LookupError):
    """The company lacks the identifier this source needs; fill it in ``watchlist.yaml``."""


class Provider[T](Protocol):
    """Fetches one kind of record for one company. Never writes to storage."""

    def fetch(self, company: Company) -> list[T]: ...
