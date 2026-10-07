"""Tests for the OpenSanctions arm of the screen.

These exist because of a real defect. OpenSanctions' default collection is not
only a watchlist: it carries corporate registries and research datasets, and
those return a perfect name score for any well-known company. With a key
configured, `Alphabet` matched at 1.00 on `gem_energy_ownership` and `Microsoft
Corporation` at 1.00 on `corp.public` / `reg.warn`. Both were reported as
sanctions HITs and scored 71.2 ELEVATED; the golden evaluation fell to 7/10.

Two things had to be true for that to happen, so both are pinned here: a match
must carry a topic asserting a designation, and its name must agree with the
subject's. Neither the provider's `score` nor its `match` flag is sufficient —
`match` came back True on every result observed, noise included.

No network: `httpx.post` is replaced, so these run in the fast suite.
"""

from __future__ import annotations

import pytest

from sentinel.tools import sanctions


class _Response:
    """The parts of an httpx response the screen actually reads."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def result(caption: str, score: float, topics: list[str], match: bool = True) -> dict:
    return {
        "id": f"id-{caption.lower().replace(' ', '-')}",
        "caption": caption,
        "score": score,
        "match": match,
        "schema": "Company",
        "datasets": ["some_dataset"],
        "properties": {"topics": topics},
    }


@pytest.fixture
def screen(monkeypatch):
    """Screens a subject against a fixed set of provider results."""

    def run(subject_name: str, results: list[dict]) -> dict:
        monkeypatch.setenv("OPENSANCTIONS_API_KEY", "test-key")
        monkeypatch.setattr(
            sanctions.httpx,
            "post",
            lambda *a, **kw: _Response({"responses": {"subject": {"results": results}}}),
        )
        return sanctions.screen_opensanctions(subject_name)

    return run


class TestRegistryEntriesAreNotDesignations:
    def test_energy_ownership_record_is_not_a_hit(self, screen):
        """The exact Alphabet false positive: perfect score, no designation topic."""
        outcome = screen("Alphabet Inc.", [result("Alphabet", 1.0, [])])
        assert outcome["status"] == "CLEAR"
        assert outcome["matches"] == []

    def test_public_company_registry_record_is_not_a_hit(self, screen):
        """The Microsoft false positive: corporate-registry and warning topics."""
        outcome = screen(
            "Microsoft Corporation",
            [result("Microsoft Corporation", 1.0, ["corp.public", "reg.action", "reg.warn"])],
        )
        assert outcome["status"] == "CLEAR"

    def test_provider_match_flag_cannot_promote_a_registry_record(self, screen):
        """`match` is set on noise too, so it must not decide the status alone."""
        outcome = screen("Alphabet Inc.", [result("Alphabet", 1.0, [], match=True)])
        assert outcome["status"] == "CLEAR"


class TestNameMustAgree:
    def test_designated_name_neighbour_is_not_the_subject(self, screen):
        """Alphabet International DMCC is genuinely debarred — and a different company."""
        outcome = screen(
            "Alphabet Inc.",
            [result("Alphabet International DMCC", 0.848, ["debarment", "sanction"])],
        )
        assert outcome["status"] == "CLEAR"

    def test_designated_subject_still_hits(self, screen):
        """The true positive must survive both gates."""
        outcome = screen(
            "Rosneft",
            [result("Open Joint-Stock Company Rosneft Oil Company", 1.0, ["sanction", "debarment"])],
        )
        assert outcome["status"] == "HIT"
        assert outcome["matches"][0]["name"].startswith("Open Joint-Stock")

    def test_exact_designated_name_hits(self, screen):
        outcome = screen("Bank Melli Iran", [result("Bank Melli Iran", 1.0, ["sanction"])])
        assert outcome["status"] == "HIT"

    def test_subject_hits_despite_an_unrelated_designated_neighbour(self, screen):
        """A real designation plus a name neighbour resolves to the real one only."""
        outcome = screen(
            "Rosneft",
            [
                result("Open Joint-Stock Company Rosneft Oil Company", 1.0, ["sanction"]),
                result("Alpha Diagnostic Services, Inc.", 0.716, ["debarment"]),
            ],
        )
        assert outcome["status"] == "HIT"
        assert len(outcome["matches"]) == 1


class TestProviderAvailability:
    def test_absent_key_reports_unavailable_not_clear(self, monkeypatch):
        """`UNAVAILABLE` (never ran) must never be reported as `CLEAR` (ran, found nothing)."""
        monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
        outcome = sanctions.screen_opensanctions("Rosneft")
        assert outcome["status"] == "UNAVAILABLE"
        assert outcome["available"] is False

    def test_transport_failure_reports_unavailable(self, monkeypatch):
        monkeypatch.setenv("OPENSANCTIONS_API_KEY", "test-key")

        def explode(*args, **kwargs):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(sanctions.httpx, "post", explode)
        outcome = sanctions.screen_opensanctions("Rosneft")
        assert outcome["status"] == "UNAVAILABLE"
        assert "connection reset" in outcome["detail"]
