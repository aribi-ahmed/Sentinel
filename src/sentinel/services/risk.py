"""Composite risk scoring.

The previous supervisor asked the model for a bare "LOW or ELEVATED" verdict.
That framing has no middle, and any critical press pushes a language model to the
cautious side, so nearly every target came back ELEVATED — which makes the
verdict carry no information.

This module replaces it with a scored model:

* Financial and sanctions exposure are computed in Python from hard numbers, so
  they cannot drift with the tone of a news article.
* The judgement dimensions — litigation, reputation, governance — are scored by
  the model against a written rubric with worked anchors, and it must name
  mitigating factors as well as aggravating ones.
* The dimensions combine into a weighted 0-100 score that lands in one of five
  bands, and the confidence reflects how much of the evidence actually arrived.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sentinel.domain import (
    Confidence,
    EvidenceKind,
    EvidencePool,
    RiskAssessment,
    RiskBand,
    RiskFactor,
    compose,
)
from sentinel.llm import AllProvidersFailed, ModelProfile, get_gateway

# Bands are open at the top: (floor, label). A score sits in the last band whose
# floor it clears.
RISK_BANDS: Tuple[Tuple[int, str], ...] = (
    (0, "MINIMAL"),
    (20, "LOW"),
    (40, "MODERATE"),
    (60, "ELEVATED"),
    (80, "SEVERE"),
)

# Sanctions exposure dominates because it is a legal bar to dealing with the
# entity at all, not a matter of degree. Governance is the lightest because it
# is inferred rather than measured.
DIMENSION_WEIGHTS: Dict[str, float] = {
    "sanctions": 0.30,
    "legal_regulatory": 0.25,
    "financial": 0.20,
    "reputational": 0.15,
    "governance": 0.10,
}

DIMENSION_LABELS: Dict[str, str] = {
    "sanctions": "Sanctions & watchlists",
    "legal_regulatory": "Legal & regulatory",
    "financial": "Financial health",
    "reputational": "Reputational",
    "governance": "Governance & controls",
}

MODEL_DIMENSIONS = ("legal_regulatory", "reputational", "governance")

# Which kinds of evidence each factor is entitled to cite. This is a deliberate
# design choice: rather than asking the model which evidence it used — and
# trusting the answer — a factor cites the evidence that, by its nature, bears
# on that dimension. The link is therefore always true, and `ungrounded_factors`
# reports honestly when a dimension had nothing to stand on.
FACTOR_EVIDENCE_KINDS: Dict[str, Tuple[EvidenceKind, ...]] = {
    "sanctions": (EvidenceKind.WATCHLIST,),
    "legal_regulatory": (EvidenceKind.FILING, EvidenceKind.POLICY),
    "financial": (EvidenceKind.MARKET_DATA,),
    "reputational": (EvidenceKind.OPEN_SOURCE,),
    "governance": (EvidenceKind.POLICY, EvidenceKind.BASELINE),
}


def _evidence_ids_for(factor_id: str, pool: EvidencePool) -> Tuple[str, ...]:
    """Evidence ids this factor is grounded in, in collection order."""
    kinds = FACTOR_EVIDENCE_KINDS.get(factor_id, ())
    return tuple(item.id for item in pool if item.kind in kinds)

# Scored dimensions the model did not return fall back to this rather than to
# zero, so a parse failure cannot silently clear a target.
_NEUTRAL_SCORE = 45

# A categorical finding — a sanctions designation, a bankruptcy petition, a
# restatement — must not be averaged away by the dimensions that are clean.
# Above this, the finding drags the composite up with it.
_ESCALATION_TRIGGER = 70.0
_ESCALATION_SHARE = 0.75

# The lowest composite that stays coherent with a given fraud band: one band
# below it. A SEVERE fraud picture cannot sit under an ELEVATED verdict, and an
# ELEVATED one cannot sit under a MODERATE verdict.
_FRAUD_BAND_FLOOR = {
    "SEVERE": 60.0,     # ELEVATED
    "ELEVATED": 40.0,   # MODERATE
    "MODERATE": 20.0,   # LOW
}


def band_for(score: float) -> str:
    """Kept as a string-returning helper; the band logic itself lives in the domain."""
    return RiskBand.for_score(score).value


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _as_number(value: Any) -> Optional[float]:
    """yfinance fills gaps with the string 'N/A', so guard every read.

    NaN needs the same treatment and is easier to miss: it is a float, so it
    passes every `is not None` check downstream, and every comparison against it
    is False. A missing profit margin therefore read as "not positive" and a
    missing P/E fell off the end of its curve at the maximum — the scorer
    invented three market signals for an entity it had no market data on.
    Non-finite means "no number", which is what `None` already means here.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _interpolate(value: float, points: List[Tuple[float, float]]) -> float:
    """Maps a metric onto a risk score through a piecewise-linear curve."""
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            span = x1 - x0
            return y0 if span == 0 else y0 + (y1 - y0) * (value - x0) / span
    return points[-1][1]


def score_financials(financial_data: Dict[str, Any]) -> Dict[str, Any]:
    """Scores balance-sheet risk from the market data, with no model involved."""
    signals: List[Dict[str, Any]] = []

    market_cap = _as_number(financial_data.get("market_cap"))
    total_debt = _as_number(financial_data.get("total_debt"))
    pe_ratio = _as_number(financial_data.get("pe_ratio"))
    free_cashflow = _as_number(financial_data.get("free_cashflow"))
    margins = _as_number(financial_data.get("profit_margins"))

    if market_cap and total_debt is not None and market_cap > 0:
        leverage = total_debt / market_cap
        score = _interpolate(leverage, [(0.0, 5), (0.15, 20), (0.40, 45), (0.80, 70), (2.0, 95)])
        signals.append({
            "metric": "Debt to market cap",
            "value": f"{leverage:.1%}",
            "score": round(score),
            "note": "Leverage relative to equity value",
        })

    if margins is not None:
        score = _interpolate(margins, [(-0.30, 95), (-0.05, 75), (0.0, 60), (0.08, 40), (0.20, 20), (0.35, 8)])
        signals.append({
            "metric": "Profit margin",
            "value": f"{margins:.1%}",
            "score": round(score),
            "note": "Negative margins signal going-concern pressure",
        })

    if pe_ratio is not None:
        # A negative P/E means the company is loss-making, which is a different
        # risk from an expensive multiple, so it is scored separately.
        if pe_ratio < 0:
            score, note = 75.0, "Negative earnings"
        else:
            score = _interpolate(pe_ratio, [(0, 35), (12, 15), (25, 25), (45, 45), (80, 70), (150, 88)])
            note = "Valuation multiple versus earnings"
        signals.append({
            "metric": "P/E ratio",
            "value": f"{pe_ratio:.1f}",
            "score": round(score),
            "note": note,
        })

    if free_cashflow is not None:
        score = 20.0 if free_cashflow > 0 else 70.0
        signals.append({
            "metric": "Free cash flow",
            "value": "Positive" if free_cashflow > 0 else "Negative",
            "score": round(score),
            "note": "Cash generation after capital spending",
        })

    if not signals:
        return {
            "score": _NEUTRAL_SCORE,
            "signals": [],
            "basis": "No market data was returned for this ticker.",
        }

    score = sum(signal["score"] for signal in signals) / len(signals)
    return {
        "score": round(_clamp(score), 1),
        "signals": signals,
        "basis": f"Derived from {len(signals)} market metric(s).",
    }


def score_sanctions(screening: Dict[str, Any]) -> Dict[str, Any]:
    """Turns an OFAC screening result into a dimension score."""
    status = (screening or {}).get("status", "UNAVAILABLE")
    matches = (screening or {}).get("matches", []) or []

    if status == "HIT":
        score, basis = 95.0, "Target matches a designated entity on the OFAC SDN list."
    elif status == "POSSIBLE_MATCH":
        score, basis = 55.0, "Name resembles a designated entity; manual adjudication required."
    elif status == "CLEAR":
        score, basis = 5.0, "No match against the OFAC SDN list."
    else:
        score, basis = 35.0, "Sanctions list unavailable — exposure could not be ruled out."

    providers = (screening or {}).get("providers") or []
    if providers:
        live = [provider["label"] for provider in providers if provider.get("available")]
        basis += f" Screened via {', '.join(live) if live else 'no provider'}."

    return {
        "score": score,
        "status": status,
        "matches": matches[:3],
        "basis": basis,
        "providers": providers,
    }


# A clean filing history is evidence of good standing, not merely absent bad news.
_CLEAN_FILER_SCORE = 15.0
# An annual report older than this suggests a reporting problem.
_STALE_ANNUAL_DAYS = 460


def score_regulatory_filings(edgar: Dict[str, Any]) -> Dict[str, Any]:
    """Scores what the entity has told its regulator, from SEC EDGAR filings.

    This is the counterweight to press-derived judgement: an 8-K Item 4.02 is
    the issuer stating its own financials cannot be relied upon, and it should
    put a floor under the regulatory score whatever the coverage looks like.
    """
    if not isinstance(edgar, dict) or not edgar.get("matched"):
        return {
            "score": None,
            "floor": 0.0,
            "signals": [],
            "assessed": False,
            "basis": (edgar or {}).get("reason", "No SEC filing history was retrieved."),
        }

    flags = edgar.get("flags") or []
    signals: List[Dict[str, Any]] = []

    for flag in flags:
        signals.append({
            "metric": flag.get("label", flag.get("code", "Filing")),
            "value": str(flag.get("date", "")),
            "score": int(flag.get("floor", 0)),
            "note": f"Form {flag.get('form', '8-K')} item {flag.get('code', '')}".strip(),
        })

    annual_age = edgar.get("annual_age_days")
    stale = isinstance(annual_age, int) and annual_age > _STALE_ANNUAL_DAYS
    if stale:
        signals.append({
            "metric": "Annual report overdue",
            "value": f"{annual_age} days",
            "score": 55,
            "note": f"Last annual filing {edgar.get('latest_annual', 'unknown')}",
        })

    floor = float(max((signal["score"] for signal in signals), default=0))

    if not signals:
        score = _CLEAN_FILER_SCORE
        basis = (
            f"{edgar.get('filings_reviewed', 0)} filings reviewed for CIK {edgar.get('cik', '')}; "
            "no supervisory events in the last 24 months."
        )
    else:
        # The worst event sets the floor; the rest lift the score toward it.
        average = sum(signal["score"] for signal in signals) / len(signals)
        score = max(floor, (floor + average) / 2)
        basis = f"{len(signals)} supervisory event(s) disclosed to the SEC in the last 24 months."

    return {
        "score": round(_clamp(score), 1),
        "floor": floor,
        "signals": signals,
        "assessed": True,
        "basis": basis,
    }


ASSESSMENT_RUBRIC = """You are the Chief Risk Officer for SENTINEL AI, scoring an entity for a
compliance file. Score conservatively in BOTH directions: overstating risk on a
sound company is as serious an error as missing a real exposure.

Score each dimension from 0 to 100 using these anchors:

  0-19  MINIMAL   No adverse signal beyond ordinary commercial activity.
 20-39  LOW       Routine disputes, standard regulatory supervision for the
                  sector, critical press without a substantiated allegation.
 40-59  MODERATE  Live litigation or a regulatory inquiry with real financial
                  exposure, but bounded and disclosed, and the entity remains
                  solvent and cooperative.
 60-79  ELEVATED  A regulator or prosecutor has made findings against the
                  entity, a material penalty is probable, or governance failures
                  are documented rather than alleged.
 80-100 SEVERE    Criminal charges, sanctions designation, proven fraud,
                  insolvency, or the entity is refusing to cooperate.

Calibration you must respect:
- Antitrust suits, privacy fines, and consumer class actions are ORDINARY for a
  large, profitable technology or financial firm. On their own they belong in
  the 25-45 range, not above 60.
- Journalistic speculation, opinion pieces, and "concerns were raised" framing
  are NOT findings. Do not score them as if a regulator had ruled.
- An allegation against a customer, supplier, or user of a platform is not an
  allegation against the entity itself.
- Scale matters: a $500M penalty against a company with a $4T market cap is a
  cost of doing business, not an existential risk.
- Where a compliance programme, board committee, or remediation is evidenced,
  it must LOWER the governance score.
- SEC filings outrank press. A clean filing history is positive evidence; a
  disclosed restatement, delisting notice or bankruptcy petition is a fact and
  outweighs any amount of favourable coverage.

Return ONLY a JSON object, with no prose or code fences:

{
  "legal_regulatory": {"score": <0-100>, "rationale": "<one sentence>"},
  "reputational":     {"score": <0-100>, "rationale": "<one sentence>"},
  "governance":       {"score": <0-100>, "rationale": "<one sentence>"},
  "key_drivers":   ["<specific factor raising risk>", "..."],
  "mitigants":     ["<specific factor lowering risk>", "..."],
  "summary": "<2-3 sentences of analytical judgement referencing the evidence>",
  "evidence_quality": "<strong|adequate|thin>"
}

Give at most four drivers and four mitigants. Every one must be grounded in the
evidence below — never invent a fact. If the evidence is thin, say so in
evidence_quality and keep the scores near the middle of the range."""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pulls the JSON object out of a model reply that may carry stray prose."""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1).strip()

    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _dimension_from_model(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    raw = payload.get(key)
    if isinstance(raw, dict):
        score = _as_number(raw.get("score"))
        rationale = str(raw.get("rationale") or "").strip()
    else:
        score = _as_number(raw)
        rationale = ""
    return {
        "score": _clamp(score) if score is not None else float(_NEUTRAL_SCORE),
        "rationale": rationale or "Not assessed — insufficient evidence returned.",
        "assessed": score is not None,
    }


def assess_with_model(
    *,
    subject: str,
    ticker: str,
    research: Any,
    financials: Dict[str, Any],
    financial_score: Dict[str, Any],
    news: Any,
    sanctions: Dict[str, Any],
    obligations: Any,
    filings: Dict[str, Any],
    edgar: Dict[str, Any],
    critique: str = "",
) -> Dict[str, Any]:
    """Asks the model for the judgement dimensions and parses its JSON reply."""
    prompt = f"""{ASSESSMENT_RUBRIC}

=== ENTITY ===
{subject} ({ticker or 'no ticker'})

=== CORPORATE BASELINE ===
{research}

=== MARKET DATA ===
{financials}
Computed financial risk score (already calculated, for your context only): {financial_score.get('score')}/100

=== SANCTIONS SCREENING ===
Status: {sanctions.get('status')} — {sanctions.get('basis')}
Matches: {sanctions.get('matches') or 'none'}

=== SEC EDGAR FILING HISTORY ===
{'Registrant: ' + str(edgar.get('company')) + ' (CIK ' + str(edgar.get('cik')) + ', ' + str(edgar.get('sic_description')) + ')' if edgar.get('matched') else 'Not an SEC registrant.'}
Computed filing risk score: {filings.get('score')} — {filings.get('basis')}
Disclosed supervisory events: {[f"{flag.get('date')} {flag.get('code')} {flag.get('label')}" for flag in (edgar.get('flags') or [])] or 'none in the last 24 months'}

=== OPEN-SOURCE FINDINGS ===
{news}

=== APPLICABLE COMPLIANCE OBLIGATIONS ===
{obligations}
{_critique_block(critique)}"""

    try:
        reply = get_gateway().complete(
            prompt,
            profile=ModelProfile.REASONING,
            temperature=0.1,
            caller="supervisor",
        )
    except AllProvidersFailed as exc:
        return {"ok": False, "error": str(exc), "payload": {}, "raw": ""}

    payload = _extract_json(reply.text)
    if payload is None:
        return {"ok": False, "error": "Model reply was not valid JSON.", "payload": {}, "raw": reply.text}
    return {"ok": True, "error": None, "payload": payload, "raw": str(reply)}


# Certainty is never available from filings, market data and open-source
# reporting. Capping below 1.0 is not false modesty: an officer who sees 100%
# has been told the system checked everything, and it did not.
CONFIDENCE_CEILING = 0.92


def _confidence(
    factors: Sequence[RiskFactor],
    pool: EvidencePool,
    *,
    model_ok: bool,
    quality: str,
) -> float:
    """How much the verdict should be trusted, on three independent axes.

    The original version divided assessed dimensions by total dimensions, which
    returned exactly 1.0 for every complete run - so a listed US issuer always
    scored 100% certain. That is a *coverage* measure wearing the label
    "confidence", and the critic flagged it on the first two live runs it saw.

    Coverage still matters, but on its own it says only that five numbers were
    produced, not that any of them rests on anything. So it is multiplied by:

    * **grounding** - the share of assessed factors that actually cite evidence;
    * **provenance** - how much of the cited evidence is high-confidence. Floored
      at 0.7, because medium-confidence sourcing is normal and should temper a
      verdict rather than gut it.
    """
    assessed = [f for f in factors if f.assessed]
    if not factors or not assessed:
        return 0.0

    coverage = len(assessed) / len(factors)
    grounding = sum(1 for f in assessed if f.is_grounded) / len(assessed)

    cited_ids = {eid for f in factors for eid in f.evidence_ids}
    cited = [item for item in pool if item.id in cited_ids]
    if cited:
        high = sum(1 for item in cited if item.confidence is Confidence.HIGH)
        provenance = 0.7 + 0.3 * (high / len(cited))
    else:
        provenance = 0.7

    confidence = coverage * grounding * provenance
    if not model_ok:
        confidence *= 0.6
    if quality.lower() == "thin":
        confidence *= 0.8

    return _clamp(confidence, 0.0, CONFIDENCE_CEILING)


def _critique_block(critique: str) -> str:
    """Renders the critic's objections for a revision pass.

    Empty on the first pass, so the ordinary prompt is unchanged and the
    revision is the only run that sees it. The framing matters: the model is
    told to *answer* the objections, not to lower its score, because a critic
    that reliably pushes scores down is a bias rather than a control.
    """
    if not critique.strip():
        return ""
    return f"""
=== INDEPENDENT REVIEW OF YOUR PREVIOUS ASSESSMENT ===
A reviewer raised the following objections. Address each one: either cite the
evidence that answers it, or revise the affected dimension. Do not change a
score you can defend - agreeing with the reviewer is not the goal, being
supportable is.

{critique.strip()}
"""


def _string_list(value: Any, limit: int = 4) -> List[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit]


def build_assessment(
    *,
    subject: str,
    ticker: str,
    research: Any,
    financials: Dict[str, Any],
    news: Any,
    sanctions: Dict[str, Any],
    obligations: Any,
    edgar: Optional[Dict[str, Any]] = None,
    evidence: Optional[EvidencePool] = None,
    critique: str = "",
    fraud: Optional[Dict[str, Any]] = None,
) -> RiskAssessment:
    """Runs the full scoring pass and returns the verdict as a domain object.

    Returning `RiskAssessment` rather than a dict is what lets the caller decide
    how to serialise it, and lets the tests assert on the model rather than on
    JSON shape. Every factor carries the ids of the evidence it rests on, so the
    chain from score to source is navigable end to end.
    """
    edgar = edgar or {}
    pool = evidence if evidence is not None else EvidencePool()

    financial = score_financials(financials or {})
    sanctions_dimension = score_sanctions(sanctions or {})
    filings = score_regulatory_filings(edgar)

    model = assess_with_model(
        subject=subject,
        ticker=ticker,
        research=research,
        financials=financials,
        financial_score=financial,
        news=news,
        sanctions=sanctions_dimension,
        obligations=obligations,
        filings=filings,
        edgar=edgar,
        critique=critique,
    )
    payload = model["payload"]

    # Computed dimensions first: these come from arithmetic and lookups, so they
    # are true regardless of what the model returned.
    scored: Dict[str, Dict[str, Any]] = {
        "financial": {
            "score": financial["score"],
            "rationale": financial["basis"],
            "assessed": bool(financial["signals"]),
            "signals": tuple(financial["signals"]),
        },
        "sanctions": {
            "score": sanctions_dimension["score"],
            "rationale": sanctions_dimension["basis"],
            "assessed": sanctions_dimension["status"] != "UNAVAILABLE",
            "signals": tuple(sanctions_dimension["matches"]),
        },
    }
    for key in MODEL_DIMENSIONS:
        judgement = _dimension_from_model(payload, key)
        scored[key] = {
            "score": judgement["score"],
            "rationale": judgement["rationale"],
            "assessed": judgement["assessed"],
            "signals": (),
        }

    # Blend the model's regulatory judgement with what the filings actually say.
    # The filing floor wins outright: a disclosed restatement is not a matter of
    # opinion, so no narrative can score below it.
    if filings["assessed"]:
        judgement = scored["legal_regulatory"]
        blended = 0.6 * judgement["score"] + 0.4 * float(filings["score"])
        scored["legal_regulatory"] = {
            "score": round(_clamp(max(blended, filings["floor"])), 1),
            "rationale": f"{judgement['rationale']} SEC filings: {filings['basis']}",
            "assessed": True,
            "signals": tuple(filings["signals"]),
        }

    # Converging fraud indicators put a floor under the regulatory dimension.
    # A narrative score cannot sit below what the disclosures jointly establish,
    # and the floor is discounted so a single flag does not dominate.
    fraud_score = float((fraud or {}).get("score") or 0.0)
    if fraud_score > 0:
        current = scored["legal_regulatory"]
        floor = fraud_score * 0.8
        if floor > current["score"]:
            patterns = (fraud or {}).get("patterns") or []
            note = (
                f" Fraud indicators score {fraud_score:.0f}/100"
                + (f" across {len(patterns)} convergence pattern(s)." if patterns else ".")
            )
            scored["legal_regulatory"] = {
                **current,
                "score": round(_clamp(floor), 1),
                "rationale": current["rationale"] + note,
                "assessed": True,
            }

    factors = tuple(
        RiskFactor(
            id=key,
            label=DIMENSION_LABELS[key],
            score=float(scored[key]["score"]),
            weight=DIMENSION_WEIGHTS[key],
            rationale=str(scored[key]["rationale"]),
            assessed=bool(scored[key]["assessed"]),
            signals=tuple(scored[key]["signals"]),
            evidence_ids=_evidence_ids_for(key, pool),
        )
        for key in sorted(DIMENSION_WEIGHTS, key=lambda name: -DIMENSION_WEIGHTS[name])
    )

    # Weighting alone would let a company in Chapter 11 read MODERATE purely
    # because it is not sanctioned, so a categorical finding lifts the composite
    # rather than being diluted by the dimensions that are clean.
    # Fraud reaches the escalation path through its *disclosed* score only:
    # press allegations lift a verdict but must not drive one on their own.
    fraud_payload = fraud or {}
    disclosed_fraud = float(fraud_payload.get("disclosed_score") or 0.0)

    hard_evidence = max(
        sanctions_dimension["score"],
        float(filings["floor"] or 0),
        disclosed_fraud,
    )
    composite, escalated = compose(
        factors,
        escalation_floor=hard_evidence,
        escalation_trigger=_ESCALATION_TRIGGER,
        escalation_share=_ESCALATION_SHARE,
    )

    # Band coherence. Reporting an overall LOW verdict while the fraud panel
    # reads ELEVATED is incoherent to anyone reading both, so the composite is
    # held to at most one band below the fraud band. The floor is the bottom of
    # that band, not the fraud score itself, because unadjudicated indicators
    # justify raising the floor rather than dictating the number.
    fraud_band = str(fraud_payload.get("band") or "")
    coherence_floor = _FRAUD_BAND_FLOOR.get(fraud_band, 0.0)
    if coherence_floor > composite:
        composite, escalated = coherence_floor, True

    composite = round(composite, 1)

    confidence = _confidence(factors, pool, model_ok=model["ok"],
                             quality=str(payload.get("evidence_quality", "")))

    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = (
            f"{subject} scores {composite}/100 on the composite model. "
            f"{model['error'] or 'The narrative assessment was unavailable, so the score rests on the computed dimensions.'}"
        )

    return RiskAssessment(
        score=composite,
        factors=factors,
        summary=summary,
        confidence=round(confidence, 2),
        evidence_quality=str(payload.get("evidence_quality") or ("thin" if not model["ok"] else "adequate")),
        key_drivers=tuple(_string_list(payload.get("key_drivers"))),
        mitigants=tuple(_string_list(payload.get("mitigants"))),
        escalated=escalated,
        escalation_note=(
            "Raised above the weighted average because a categorical finding "
            "(sanctions designation, bankruptcy, delisting or restatement) "
            "cannot be offset by the dimensions that are clean."
            if escalated else ""
        ),
        model_ok=model["ok"],
        model_error=model["error"] or "",
    )
