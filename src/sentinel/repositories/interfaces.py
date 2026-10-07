"""Persistence contracts, expressed in domain language.

Services and agents depend on these Protocols, never on a SQLAlchemy `Session`.
Two payoffs the brief calls out (§7, §10.2):

* The domain stays SQL-free, so storage can evolve without touching business
  logic — swapping Postgres for anything else is a change in one package.
* Unit tests inject `InMemoryInvestigationRepository` and run in milliseconds
  with no database at all, which is what makes the test pyramid affordable.

`Protocol` rather than `ABC` on purpose: implementations do not need to inherit
anything, so a test fake is just a class that happens to have the right methods.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from sentinel.domain.investigation import Investigation


@runtime_checkable
class InvestigationRepository(Protocol):
    """The system of record for investigations."""

    def add(self, investigation: Investigation) -> Investigation:
        """Persists a new investigation and returns it as stored."""
        ...

    def get(self, investigation_id: str) -> Optional[Investigation]:
        """Returns one investigation, or None when the id is unknown."""
        ...

    def save(self, investigation: Investigation) -> Investigation:
        """Persists changes to an investigation that already exists."""
        ...

    def list_recent(self, limit: int = 200) -> List[Investigation]:
        """Returns investigations newest first, for the audit ledger."""
        ...

    def count(self) -> int:
        """Total investigations on record."""
        ...
