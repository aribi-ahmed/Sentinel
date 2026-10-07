"""Independent review of the supervisor's verdict.

Six of the seven checks are deterministic: they read the assessment, the
evidence pool and the tool audit trail and either find a defect or do not. No
model is asked whether the verdict is sound, since a model asked that question
returns plausible commentary regardless.

The seventh check is model-judged and narrow — which stated drivers the cited
evidence fails to support — and its answers are matched back against the
assessment's own driver list. If the provider is unreachable the review still
stands on the deterministic checks.

`_check_unverified_scope` depends on the tool audit trail: it catches a
dimension scored low because the tool that would have found risk never ran,
which is indistinguishable from a genuine clean result in the assessment alone.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from sentinel.domain.analysis import RiskAssessment, RiskBand
from sentinel.domain.evidence import Confidence, EvidencePool
from sentinel.domain.planning import InvestigationPlan
from sentinel.domain.review import (
    Challenge,
    ChallengeKind,
    ChallengeSeverity,
    CriticReview,
    ReviewVerdict,
    decide_verdict,
)
from sentinel.domain.tooling import ToolAuditTrail, ToolOutcome

# Which risk dimension each tool feeds. A tool that did not run leaves its
# dimension resting on absence of evidence rather than on evidence of absence.
TOOL_DIMENSION: Dict[str, str] = {
    "screen_sanctions": "sanctions",
    "fetch_sec_filings": "legal_regulatory",
    "fetch_company_financials": "financial",
    "search_company_news": "reputational",
    "build_compliance_brief": "governance",
}

# A dimension scoring at or below this is making a positive claim of safety.
# Above it, the verdict is already cautious and a missing tool changes less.
REASSURING_SCORE = 40.0

# A factor carrying at least this much weight is material when ungrounded;
# below it, the same defect is worth noting but does not qualify the verdict.
MATERIAL_WEIGHT = 0.20

# Confidence deductions, capped so a pile of advisories cannot zero a verdict.
PENALTY = {
    ChallengeSeverity.BLOCKING: 0.15,
    ChallengeSeverity.MATERIAL: 0.05,
    ChallengeSeverity.ADVISORY: 0.0,
}
MAX_PENALTY = 0.40


# --- deterministic checks ----------------------------------------------------

def _check_citations(assessment: RiskAssessment, pool: EvidencePool) -> List[Challenge]:
    """Every cited evidence id must resolve. The ADR-007 regression guard."""
    challenges: List[Challenge] = []

    for factor in assessment.factors:
        dangling = [eid for eid in factor.evidence_ids if eid not in pool]
        if dangling:
            challenges.append(Challenge(
                kind=ChallengeKind.DANGLING_CITATION,
                severity=ChallengeSeverity.BLOCKING,
                subject=factor.id,
                detail=(
                    f"The {factor.label} factor cites {len(dangling)} evidence "
                    f"record(s) that do not exist in the pool: {', '.join(dangling[:3])}."
                ),
                remedy="Re-derive the factor from evidence that is actually present.",
            ))
    return challenges


def _check_grounding(assessment: RiskAssessment) -> List[Challenge]:
    """A scored factor resting on judgement alone."""
    challenges: List[Challenge] = []

    for factor in assessment.ungrounded_factors():
        material = factor.weight >= MATERIAL_WEIGHT
        challenges.append(Challenge(
            kind=ChallengeKind.UNGROUNDED_FACTOR,
            severity=ChallengeSeverity.MATERIAL if material else ChallengeSeverity.ADVISORY,
            subject=factor.id,
            detail=(
                f"The {factor.label} factor scores {factor.score:.0f}/100 at "
                f"{int(factor.weight * 100)}% weight but cites no evidence; it rests "
                "on judgement alone."
            ),
            remedy=f"Cite the evidence behind the {factor.label} score, or mark it unassessed.",
        ))
    return challenges


def _check_band(assessment: RiskAssessment) -> List[Challenge]:
    """The band must follow arithmetically from the score."""
    expected = RiskBand.for_score(assessment.score)
    if expected is assessment.band:
        return []

    return [Challenge(
        kind=ChallengeKind.BAND_MISMATCH,
        severity=ChallengeSeverity.BLOCKING,
        subject="band",
        detail=(
            f"The verdict reports {assessment.band.value} but a score of "
            f"{assessment.score:.1f} falls in {expected.value}."
        ),
        remedy="Recompute the band from the composite score.",
    )]


def _check_unverified_scope(
    assessment: RiskAssessment,
    trail: Optional[ToolAuditTrail],
) -> List[Challenge]:
    """A reassuring score produced by a tool that never ran.

    This is the check the audit trail made possible, and the one most likely to
    catch a genuinely dangerous verdict. A sanctions dimension scoring 10/100
    because the list was screened and came back clean, and one scoring 10/100
    because no list was ever consulted, are indistinguishable in the assessment.
    They are not indistinguishable in the trail.
    """
    if trail is None:
        return []

    challenges: List[Challenge] = []

    for call in trail:
        if call.outcome not in (ToolOutcome.UNAVAILABLE, ToolOutcome.FAILED):
            continue

        dimension_id = TOOL_DIMENSION.get(call.tool)
        factor = assessment.factor(dimension_id) if dimension_id else None
        if factor is None or not factor.assessed:
            continue

        if factor.score > REASSURING_SCORE:
            # The verdict is already cautious here; the gap is worth noting only.
            challenges.append(Challenge(
                kind=ChallengeKind.UNVERIFIED_SCOPE,
                severity=ChallengeSeverity.ADVISORY,
                subject=call.tool,
                detail=(
                    f"{call.tool} did not return data ({call.error or call.outcome.value}), "
                    f"so the {factor.label} score of {factor.score:.0f} rests on a partial view."
                ),
                remedy="Re-run once the source is available to confirm the score.",
            ))
            continue

        # A low score plus a tool that never ran is a claim of safety that
        # nothing checked. This is the dangerous shape.
        challenges.append(Challenge(
            kind=ChallengeKind.UNVERIFIED_SCOPE,
            severity=ChallengeSeverity.BLOCKING,
            subject=call.tool,
            detail=(
                f"The {factor.label} factor scores a reassuring {factor.score:.0f}/100, "
                f"but {call.tool} never returned data ({call.error or call.outcome.value}). "
                "That is absence of evidence being read as evidence of absence."
            ),
            remedy=f"Restore {call.tool} and re-run, or mark {factor.label} unassessed.",
        ))

    return challenges


def _check_evidence_quality(assessment: RiskAssessment, pool: EvidencePool) -> List[Challenge]:
    """A serious verdict should rest on something better than inference."""
    if assessment.band < RiskBand.ELEVATED:
        return []

    cited = pool.resolve(assessment.cited_evidence_ids())
    high = [item for item in cited if item.confidence is Confidence.HIGH]
    if high:
        return []

    return [Challenge(
        kind=ChallengeKind.THIN_EVIDENCE,
        severity=ChallengeSeverity.MATERIAL,
        subject="evidence",
        detail=(
            f"A {assessment.band.value} verdict rests on {len(cited)} cited record(s), "
            "none of them high-confidence: no filing, market metric or watchlist hit "
            "supports it."
        ),
        remedy="Support the verdict with a filing or screening result before release.",
    )]


# Above this, the verdict is claiming near-certainty. Observed live: the
# supervisor returned confidence 1.0 on an entity whose own drivers the critic
# then found unsupported. No open-source evidence base earns 100%.
CERTAINTY_CEILING = 0.95


def _check_confidence(
    assessment: RiskAssessment,
    pool: EvidencePool,
    trail: Optional[ToolAuditTrail],
    plan: Optional[InvestigationPlan],
) -> List[Challenge]:
    """Confidence must reflect how much of the intended work actually happened."""
    challenges: List[Challenge] = []

    if assessment.confidence >= CERTAINTY_CEILING:
        challenges.append(Challenge(
            kind=ChallengeKind.OVERCONFIDENT,
            severity=ChallengeSeverity.ADVISORY,
            subject="confidence",
            detail=(
                f"Confidence is reported at {int(assessment.confidence * 100)}%. An "
                "assessment built from filings, market data and open-source reporting "
                "cannot be certain; some residual doubt always remains."
            ),
            remedy="Cap reported confidence below certainty.",
        ))

    if assessment.confidence <= 0.75:
        return challenges

    reasons: List[str] = []
    if plan is not None and not plan.is_complete:
        reasons.append(f"the plan was reduced ({len(plan.skipped)} specialist(s) skipped)")
    if trail is not None and trail.failures:
        names = ", ".join(sorted({c.tool for c in trail.failures}))
        reasons.append(f"{len(trail.failures)} tool call(s) returned no data ({names})")
    if len(pool) < 5:
        reasons.append(f"only {len(pool)} evidence record(s) were gathered")

    if not reasons:
        return challenges

    challenges.append(Challenge(
        kind=ChallengeKind.OVERCONFIDENT,
        severity=ChallengeSeverity.MATERIAL,
        subject="confidence",
        detail=(
            f"Confidence is reported at {int(assessment.confidence * 100)}% although "
            + "; ".join(reasons) + "."
        ),
        remedy="Lower the stated confidence to match the evidence actually gathered.",
    ))
    return challenges


DETERMINISTIC_CHECKS = (
    _check_citations,
    _check_grounding,
    _check_band,
    _check_unverified_scope,
    _check_evidence_quality,
    _check_confidence,
)


# --- the model-judged check --------------------------------------------------

CHALLENGE_PROMPT = """You are an independent reviewer auditing a risk assessment. You did not
write it, and your job is to find claims it cannot support - not to agree with it.

Below are the drivers the assessment states, and the complete evidence it cites.

A driver is UNSUPPORTED if nothing in the evidence would let a reader verify it.
A driver that is merely brief, or that generalises correctly from the evidence,
is SUPPORTED. Do not object to wording. Object only to claims with no basis.

=== STATED DRIVERS ===
{drivers}

=== EVIDENCE CITED ===
{evidence}

Reply with JSON only, no prose:
{{"unsupported": [{{"driver": "<copy the driver text exactly>", "why": "<what the evidence fails to show>"}}]}}

If every driver is supported, reply {{"unsupported": []}}.
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Recovers the JSON object from a reply that may be fenced or prefixed."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _challenge_drivers(
    assessment: RiskAssessment,
    pool: EvidencePool,
) -> Tuple[List[Challenge], bool, str]:
    """Asks the model which stated drivers the evidence does not support.

    Returns `(challenges, model_ok, error)`. Every returned driver is matched
    back against the assessment's own driver list, so the model cannot invent an
    objection to something the verdict never claimed.
    """
    from sentinel.llm import get_gateway
    from sentinel.llm.contracts import ModelProfile
    from sentinel.llm.gateway import AllProvidersFailed

    drivers = [d for d in assessment.key_drivers if d.strip()]
    cited = pool.resolve(assessment.cited_evidence_ids())
    if not drivers or not cited:
        return [], True, ""

    prompt = CHALLENGE_PROMPT.format(
        drivers="\n".join(f"- {d}" for d in drivers),
        evidence="\n".join(
            f"- [{item.kind.value}/{item.confidence.value}] {item.summary} (source: {item.source})"
            for item in cited[:25]
        ),
    )

    try:
        reply = get_gateway().complete(
            prompt,
            profile=ModelProfile.REASONING,
            temperature=0.0,
            caller="critic",
        )
    except AllProvidersFailed as exc:
        return [], False, str(exc)
    except Exception as exc:  # a critic outage must never fail an investigation
        return [], False, f"{type(exc).__name__}: {exc}"

    payload = _extract_json(reply.text)
    if payload is None:
        return [], False, "Critic reply was not valid JSON."

    challenges: List[Challenge] = []
    for row in payload.get("unsupported") or []:
        if not isinstance(row, dict):
            continue
        claimed = str(row.get("driver") or "").strip()
        why = str(row.get("why") or "").strip()
        # Anchor: only accept an objection to a driver the assessment actually made.
        match = next((d for d in drivers if d.strip() == claimed), None)
        if match is None:
            match = next((d for d in drivers if claimed and claimed.lower() in d.lower()), None)
        if match is None or not why:
            continue
        challenges.append(Challenge(
            kind=ChallengeKind.UNSUPPORTED_DRIVER,
            severity=ChallengeSeverity.MATERIAL,
            subject=match,
            detail=f'The driver "{match}" is not supported by the cited evidence: {why}',
            remedy="Cite evidence for this driver, or remove it from the verdict.",
        ))

    return challenges, True, ""


# --- entry point -------------------------------------------------------------

def review(
    assessment: RiskAssessment,
    *,
    pool: Optional[EvidencePool] = None,
    trail: Optional[ToolAuditTrail] = None,
    plan: Optional[InvestigationPlan] = None,
    revision: int = 0,
    use_model: bool = True,
) -> CriticReview:
    """Runs every check over one assessment and returns the review.

    Never raises. A critic that can crash an investigation is worse than no
    critic: it turns a quality control into a availability risk.
    """
    pool = pool if pool is not None else EvidencePool()
    challenges: List[Challenge] = []

    for check in DETERMINISTIC_CHECKS:
        try:
            if check is _check_unverified_scope:
                challenges.extend(check(assessment, trail))
            elif check is _check_confidence:
                challenges.extend(check(assessment, pool, trail, plan))
            elif check in (_check_citations, _check_evidence_quality):
                challenges.extend(check(assessment, pool))
            else:
                challenges.extend(check(assessment))
        except Exception as exc:  # one broken check must not void the review
            challenges.append(Challenge(
                kind=ChallengeKind.UNGROUNDED_FACTOR,
                severity=ChallengeSeverity.ADVISORY,
                subject=getattr(check, "__name__", "check"),
                detail=f"A critic check failed to run: {type(exc).__name__}: {exc}",
                remedy="Investigate the critic; this review is incomplete.",
            ))

    model_ok, model_error = True, ""
    if use_model:
        model_challenges, model_ok, model_error = _challenge_drivers(assessment, pool)
        challenges.extend(model_challenges)

    frozen = tuple(challenges)
    penalty = min(MAX_PENALTY, sum(PENALTY[c.severity] for c in frozen))

    return CriticReview(
        verdict=decide_verdict(frozen),
        challenges=frozen,
        confidence_penalty=round(penalty, 2),
        revision=revision,
        model_ok=model_ok,
        model_error=model_error,
    )
