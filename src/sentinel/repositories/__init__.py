"""Persistence, behind interfaces.

`interfaces` declares the contract in domain language; `memory` and `sql`
implement it. Callers depend on the Protocol, so which one they get is a wiring
decision, not a code change.

`sql` is imported lazily via `__getattr__` so that importing this package — or
running the unit suite against the in-memory implementation — does not drag in
SQLAlchemy.
"""

from typing import Any

from sentinel.repositories.interfaces import InvestigationRepository
from sentinel.repositories.memory import InMemoryInvestigationRepository

__all__ = [
    "InvestigationRepository",
    "InMemoryInvestigationRepository",
    "SqlInvestigationRepository",
]


def __getattr__(name: str) -> Any:
    if name == "SqlInvestigationRepository":
        from sentinel.repositories.sql import SqlInvestigationRepository

        return SqlInvestigationRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
