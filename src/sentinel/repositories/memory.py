"""In-memory investigation repository.

Not a toy: this is the implementation unit tests run against, so it has to
honour the same contract as the SQL one — including the detail that reads
return snapshots rather than live references, so a caller mutating what it got
back cannot corrupt the store. A fake that is more permissive than the real
thing lets bugs through, which defeats the point of having one.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from sentinel.domain.investigation import Investigation


class InMemoryInvestigationRepository:
    """Dict-backed store honouring `InvestigationRepository`."""

    __slots__ = ("_items",)

    def __init__(self, items: Optional[List[Investigation]] = None) -> None:
        self._items: Dict[str, Investigation] = {item.id: item for item in (items or [])}

    def add(self, investigation: Investigation) -> Investigation:
        if investigation.id in self._items:
            raise ValueError(f"Investigation {investigation.id} already exists.")
        self._items[investigation.id] = investigation
        return investigation

    def get(self, investigation_id: str) -> Optional[Investigation]:
        return self._items.get(investigation_id)

    def save(self, investigation: Investigation) -> Investigation:
        if investigation.id not in self._items:
            raise KeyError(f"Investigation {investigation.id} does not exist; use add().")
        self._items[investigation.id] = investigation
        return investigation

    def list_recent(self, limit: int = 200) -> List[Investigation]:
        ordered = sorted(self._items.values(), key=lambda item: item.created_at, reverse=True)
        return ordered[:limit]

    def count(self) -> int:
        return len(self._items)
