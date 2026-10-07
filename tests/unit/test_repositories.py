"""Repository contract tests.

Both implementations are exercised through the same test body. That is the point
of the interface: if the in-memory fake and the SQL repository can diverge, unit
tests that use the fake stop being evidence about production behaviour.

The SQL case is marked `integration` and skips when Postgres is not up, so the
fast suite still runs anywhere.
"""

from __future__ import annotations

import pytest

from sentinel.config.settings import settings

from sentinel.domain import (
    HumanDecision,
    Investigation,
    RiskAssessment,
    RiskFactor,
    Subject,
    Verdict,
)
from sentinel.repositories import InMemoryInvestigationRepository, InvestigationRepository


def assessment(score: float = 67.5) -> RiskAssessment:
    return RiskAssessment(
        score=score,
        factors=(RiskFactor(id="sanctions", label="Sanctions", score=95, weight=0.3,
                            rationale="Hit", evidence_ids=("ev_1",)),),
        summary="Designated entity.",
        confidence=1.0,
    )


@pytest.fixture
def repo() -> InvestigationRepository:
    return InMemoryInvestigationRepository()


class TestInvestigationRepositoryContract:
    def test_satisfies_the_protocol(self, repo: InvestigationRepository) -> None:
        assert isinstance(repo, InvestigationRepository)

    def test_add_then_get_round_trips(self, repo: InvestigationRepository) -> None:
        stored = repo.add(Investigation(subject=Subject(name="Rosneft", ticker="ROSN")))
        loaded = repo.get(stored.id)

        assert loaded is not None
        assert loaded.subject.name == "Rosneft"
        assert loaded.subject.ticker == "ROSN"

    def test_get_returns_none_for_an_unknown_id(self, repo: InvestigationRepository) -> None:
        assert repo.get("inv_nonexistent") is None

    def test_adding_the_same_id_twice_is_refused(self, repo: InvestigationRepository) -> None:
        investigation = Investigation(subject=Subject(name="Acme"))
        repo.add(investigation)

        with pytest.raises(ValueError):
            repo.add(investigation)

    def test_saving_an_unknown_investigation_is_refused(self, repo: InvestigationRepository) -> None:
        with pytest.raises(KeyError):
            repo.save(Investigation(subject=Subject(name="Ghost")))

    def test_save_persists_the_assessment_and_decision(self, repo: InvestigationRepository) -> None:
        stored = repo.add(Investigation(subject=Subject(name="Wolfspeed", ticker="WOLF")))
        decided = stored.with_assessment(assessment()).with_decision(
            HumanDecision(verdict=Verdict.APPROVED), report_markdown="# Report"
        )

        repo.save(decided)
        loaded = repo.get(stored.id)

        assert loaded is not None
        assert loaded.status.is_terminal
        assert loaded.has_report

    def test_list_recent_is_newest_first_and_respects_the_limit(
        self, repo: InvestigationRepository
    ) -> None:
        for index in range(5):
            repo.add(Investigation(subject=Subject(name=f"Entity {index}")))

        recent = repo.list_recent(limit=3)

        assert len(recent) == 3
        assert recent == sorted(recent, key=lambda item: item.created_at, reverse=True)

    def test_count_tracks_additions(self, repo: InvestigationRepository) -> None:
        assert repo.count() == 0
        repo.add(Investigation(subject=Subject(name="Acme")))
        assert repo.count() == 1


@pytest.mark.integration
@pytest.mark.skipif(
    not str(settings.DATABASE_URL).startswith("postgres"),
    reason="needs Postgres; run `docker compose up -d`",
)
class TestSqlRepositoryHonoursTheSameContract:
    def test_round_trips_through_postgres(self) -> None:
        from sentinel.database.db import SessionLocal, init_db
        from sentinel.repositories import SqlInvestigationRepository

        init_db()
        with SessionLocal() as session:
            repo = SqlInvestigationRepository(session)

            assert isinstance(repo, InvestigationRepository)

            stored = repo.add(Investigation(subject=Subject(name="Contract Test Ltd", ticker="CTL")))
            loaded = repo.get(stored.id)

            assert loaded is not None
            assert loaded.subject.name == "Contract Test Ltd"

            decided = loaded.with_assessment(assessment()).with_decision(
                HumanDecision(verdict=Verdict.APPROVED), report_markdown="# Contract"
            )
            repo.save(decided)

            reloaded = repo.get(stored.id)
            assert reloaded is not None
            assert reloaded.has_report
            assert reloaded.status.is_terminal
