"""Central tool registry (M-04): one call path, one audit record.

Every external tool is invoked through `ToolRegistry.invoke`, which:

* classifies the return value into one of four outcomes, so a tool reporting its
  own failure as ordinary data (`search_company_news` returns a warning string
  when unconfigured) is no longer read as a result;
* records a `ToolInvocation` with arguments, duration and outcome;
* returns the spec's declared fallback on any runtime fault rather than raising,
  so a failing tool degrades the investigation visibly instead of ending it.

Tools are not exposed as an agent-callable schema. The specialists have fixed
responsibilities, and letting a model select tools freely would add
nondeterminism to a reproducible part of the pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from sentinel.domain.tooling import (
    MAX_DIGEST_CHARS,
    ToolAuditTrail,
    ToolInvocation,
    ToolOutcome,
    redact_arguments,
)

Classification = Tuple[ToolOutcome, str]


class ToolCategory(str, Enum):
    """What kind of question a tool answers — used to group the catalogue."""

    SCREENING = "screening"    # watchlists and sanctions
    FILINGS = "filings"        # regulatory disclosure
    MARKET = "market"          # quantitative market data
    OSINT = "osint"            # open-web reporting
    POLICY = "policy"          # retrieval over the framework corpus
    MEMORY = "memory"          # prior investigations of the same entity
    ANALYSIS = "analysis"      # derivation over evidence already collected


def _always_available() -> Tuple[bool, str]:
    return True, ""


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool: how to call it, and how to read what it returns."""

    name: str
    summary: str
    category: ToolCategory
    fn: Callable[..., Any]
    # Reads the return value and says what actually happened. Declared per tool
    # because only the tool's author knows what its "nothing found" looks like.
    classify: Callable[[Any], Classification]
    # The shape returned when the tool cannot run, so callers never branch on None.
    fallback: Callable[[], Any] = dict
    # Whether the tool *can* run right now — a missing key or dataset is a
    # deployment fact, knowable before the call rather than after it fails.
    availability: Callable[[], Tuple[bool, str]] = _always_available
    # Extra argument names to mask, beyond the always-redacted defaults.
    redact: Tuple[str, ...] = ()
    # Where the data comes from — shown in the catalogue and the report.
    data_source: str = ""

    def describe(self) -> Dict[str, Any]:
        available, reason = self.check_availability()
        return {
            "name": self.name,
            "summary": self.summary,
            "category": self.category.value,
            "data_source": self.data_source,
            "available": available,
            "unavailable_reason": reason,
        }

    def check_availability(self) -> Tuple[bool, str]:
        """Never raises: a broken probe reports as unavailable, not as a crash."""
        try:
            return self.availability()
        except Exception as exc:
            return False, f"availability check failed: {type(exc).__name__}: {exc}"


@dataclass
class ToolResult:
    """What a caller gets back: the data, and the record of how it was obtained."""

    data: Any
    invocation: ToolInvocation

    @property
    def ok(self) -> bool:
        return self.invocation.ok

    @property
    def outcome(self) -> ToolOutcome:
        return self.invocation.outcome

    @property
    def reason(self) -> str:
        return self.invocation.error


class ToolRegistry:
    """The catalogue, and the single path through which tools are called."""

    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise ValueError(f"A tool named {spec.name!r} is already registered.")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            known = ", ".join(sorted(self._specs)) or "none"
            raise KeyError(f"No tool named {name!r} is registered. Known tools: {known}.") from None

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self._specs))

    def describe(self) -> List[Dict[str, Any]]:
        """The catalogue, for the API and for the report's tool inventory."""
        return [self._specs[name].describe() for name in self.names]

    def invoke(
        self,
        name: str,
        *,
        caller: str,
        trail: Optional[ToolAuditTrail] = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Calls a registered tool and records the call.

        Raises only for an *unregistered* name, which is a programming error and
        should fail loudly. Every runtime fault — a missing key, a network
        error, an exception inside the tool — comes back as a `ToolResult`
        carrying the fallback shape and a recorded reason.
        """
        spec = self.get(name)
        arguments = redact_arguments(kwargs, extra=spec.redact)

        available, reason = spec.check_availability()
        if not available:
            return self._record(
                trail,
                spec.fallback(),
                ToolInvocation(
                    tool=spec.name,
                    caller=caller,
                    outcome=ToolOutcome.UNAVAILABLE,
                    arguments=arguments,
                    error=reason or "Tool is not available in this deployment.",
                ),
            )

        started = time.perf_counter()
        try:
            data = spec.fn(**kwargs)
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            return self._record(
                trail,
                spec.fallback(),
                ToolInvocation(
                    tool=spec.name,
                    caller=caller,
                    outcome=ToolOutcome.FAILED,
                    arguments=arguments,
                    duration_ms=elapsed,
                    error=f"{type(exc).__name__}: {exc}"[:MAX_DIGEST_CHARS],
                ),
            )

        elapsed = round((time.perf_counter() - started) * 1000, 3)
        outcome, detail = _classify_safely(spec, data)
        detail = detail[:MAX_DIGEST_CHARS]

        return self._record(
            trail,
            data,
            ToolInvocation(
                tool=spec.name,
                caller=caller,
                outcome=outcome,
                arguments=arguments,
                duration_ms=elapsed,
                result_digest="" if outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) else detail,
                error=detail if outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) else "",
            ),
        )

    @staticmethod
    def _record(
        trail: Optional[ToolAuditTrail],
        data: Any,
        invocation: ToolInvocation,
    ) -> ToolResult:
        if trail is not None:
            trail.add(invocation)
        return ToolResult(data=data, invocation=invocation)


def _classify_safely(spec: ToolSpec, data: Any) -> Classification:
    """A classifier that raises must not turn a good result into a crash."""
    try:
        outcome, detail = spec.classify(data)
        if outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) and not str(detail).strip():
            detail = f"{spec.name} reported {outcome.value} without a reason."
        return outcome, str(detail)
    except Exception as exc:
        return ToolOutcome.OK, f"classifier error ({type(exc).__name__}); result passed through"


# --- classifiers -------------------------------------------------------------
#
# Each reads one tool's return value. They are the part of this module worth
# reviewing closely: they decide whether a specialist believes it has data.

def classify_sanctions(data: Any) -> Classification:
    """CLEAR is a real answer, not an empty one — the entity *was* screened.

    The digest names how many providers actually ran. Partial coverage is worth
    recording: a CLEAR from one list is a weaker statement than a CLEAR from two,
    and the audit trail is the only place that distinction survives.
    """
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "screening returned a non-dict result"

    providers = [p for p in (data.get("providers") or []) if isinstance(p, dict)]
    ran = [p for p in providers if p.get("available")]
    coverage = f"{len(ran)}/{len(providers)} providers" if providers else "no providers"

    if not data.get("screened") or not ran:
        detail = "; ".join(
            f"{p.get('label') or p.get('id')}: {p.get('detail') or 'unavailable'}" for p in providers
        )
        return ToolOutcome.UNAVAILABLE, f"no watchlist provider ran ({detail or 'none configured'})"

    status = str(data.get("status") or "").upper()
    if status == "HIT":
        return ToolOutcome.OK, f"HIT: {len(data.get('matches') or [])} match(es), {coverage}"
    if status == "CLEAR":
        return ToolOutcome.OK, f"CLEAR: no match, {coverage}"
    return ToolOutcome.FAILED, f"unrecognised screening status {status or 'missing'} ({coverage})"


def classify_edgar(data: Any) -> Classification:
    """No SEC registrant is a finding about the entity, not a tool failure.

    An unreachable EDGAR is the opposite: nothing was established either way. It
    is reported UNAVAILABLE so the absence of filings cannot be read as evidence
    of a clean filing history — the SEC rate-limits, so this path is reached in
    normal operation, not only in a outage.
    """
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "EDGAR returned a non-dict result"
    if data.get("matched"):
        flags = data.get("flags") or []
        return ToolOutcome.OK, f"CIK {data.get('cik')} matched; {len(flags)} supervisory flag(s)"
    if data.get("reachable") is False:
        return ToolOutcome.UNAVAILABLE, str(data.get("reason") or "EDGAR was unreachable.")
    return ToolOutcome.EMPTY, str(data.get("reason") or "No SEC registrant matched.")


def classify_financials(data: Any) -> Classification:
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "market data returned a non-dict result"
    if data.get("error"):
        return ToolOutcome.FAILED, str(data["error"])

    known = [k for k in ("market_cap", "pe_ratio", "total_debt", "free_cashflow", "profit_margins")
             if data.get(k) not in (None, "", "N/A")]
    if not known:
        return ToolOutcome.EMPTY, f"no metrics available for {data.get('symbol') or 'the ticker'}"
    return ToolOutcome.OK, f"{data.get('company_name') or data.get('symbol')}: {len(known)}/5 metrics"


def classify_news(data: Any) -> Classification:
    """The important one.

    This tool returns a *string* in every case, including its own failures. Left
    unclassified, `"OSINT Warning: TAVILY_API_KEY missing"` reaches the model as
    though it were reporting on the entity.
    """
    text = data if isinstance(data, str) else str(data)
    stripped = text.strip()

    if stripped.startswith("OSINT Warning:"):
        return ToolOutcome.UNAVAILABLE, stripped
    if stripped.startswith("News search failed:"):
        return ToolOutcome.FAILED, stripped
    if not stripped or stripped in ("[]", "()", "{}"):
        return ToolOutcome.EMPTY, "no open-source reporting matched the query"
    return ToolOutcome.OK, f"{len(stripped)} chars of open-source reporting"


def classify_compliance_brief(data: Any) -> Classification:
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "compliance retrieval returned a non-dict result"

    retrieval = str(data.get("retrieval") or "")
    obligations = data.get("obligations") or []
    sources = data.get("sources") or []

    if retrieval == "unavailable":
        return ToolOutcome.UNAVAILABLE, str(data.get("error") or "no policy corpus is indexed")
    if data.get("error"):
        # Retrieval worked, synthesis did not — degraded, but it did run.
        return ToolOutcome.EMPTY, f"retrieved {len(sources)} passages; synthesis failed: {data['error']}"
    return ToolOutcome.OK, f"{len(obligations)} obligation(s) from {len(sources)} passage(s) via {retrieval}"


def classify_history(data: Any) -> Classification:
    """No prior record is a real answer: the entity has not been seen before."""
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "entity recall returned a non-dict result"

    count = int(data.get("count") or 0)
    if count == 0:
        return ToolOutcome.EMPTY, "no prior review of this entity on record"
    return ToolOutcome.OK, f"{count} prior review(s), trend {data.get('trend_label', 'unknown').lower()}"


def classify_fraud(data: Any) -> Classification:
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "fraud assessment returned a non-dict result"

    signals = data.get("signals") or []
    if not signals:
        return ToolOutcome.EMPTY, "no fraud indicators found in the reviewed disclosures"

    patterns = data.get("patterns") or []
    return ToolOutcome.OK, (
        f"{len(signals)} indicator(s) across {len(data.get('categories') or [])} categor(ies), "
        f"{len(patterns)} pattern(s), scoring {data.get('score')}/100"
    )


def classify_network(data: Any) -> Classification:
    if not isinstance(data, dict):
        return ToolOutcome.FAILED, "network resolution returned a non-dict result"

    nodes = int(data.get("node_count") or 0)
    # The subject alone is not a network.
    if nodes <= 1:
        return ToolOutcome.EMPTY, "no connected entities were resolved"

    flagged = int(data.get("flagged_count") or 0)
    detail = f"{nodes} entities across {data.get('edge_count')} relationships"
    if flagged:
        detail += f", {flagged} carrying a watchlist association"
    return ToolOutcome.OK, detail


def classify_review(data: Any) -> Classification:
    if not isinstance(data, dict) or not data.get("verdict"):
        return ToolOutcome.FAILED, "review returned no verdict"

    challenges = data.get("challenges") or []
    if not challenges:
        return ToolOutcome.OK, "endorsed: no objection raised"
    return ToolOutcome.OK, (
        f"{data['verdict']}: {data.get('blocking', 0)} blocking, "
        f"{data.get('material', 0)} material objection(s)"
    )


# --- availability probes -----------------------------------------------------

def _tavily_configured() -> Tuple[bool, str]:
    from sentinel.config.settings import settings

    if not (settings.TAVILY_API_KEY or "").strip():
        return False, "TAVILY_API_KEY is not set; open-source reporting is unavailable."
    return True, ""


def _sanctions_data_present() -> Tuple[bool, str]:
    from sentinel.tools.sanctions import SDN_PATH

    if not SDN_PATH.is_file():
        return False, f"The OFAC SDN dataset is missing from {SDN_PATH.parent}; screening cannot run."
    return True, ""


def _corpus_indexed() -> Tuple[bool, str]:
    from sentinel.tools.rag import PERSIST_DIR

    if not PERSIST_DIR.is_dir() or not any(PERSIST_DIR.iterdir()):
        return False, "No compliance index found; run `python ingest_compliance.py` to build it."
    return True, ""


def _sec_identified() -> Tuple[bool, str]:
    """EDGAR rejects requests without a contactable User-Agent."""
    from sentinel.config.settings import settings

    agent = (settings.SEC_USER_AGENT or "").strip()
    if "@" not in agent:
        return False, "SEC_USER_AGENT must carry a contact address; EDGAR rejects anonymous clients."
    return True, ""


def _ledger_reachable() -> Tuple[bool, str]:
    """The investigation ledger backs entity recall."""
    try:
        from sentinel.database.db import SessionLocal

        session = SessionLocal()
        session.close()
        return True, ""
    except Exception as exc:
        return False, f"The investigation ledger is unreachable: {type(exc).__name__}."


# --- the default registry ----------------------------------------------------

def build_default_registry() -> ToolRegistry:
    """Registers SENTINEL's five tools.

    Imports happen inside the function so that constructing a registry in a test
    does not pull in yfinance, chromadb and torch.
    """
    from sentinel.tools.edgar import fetch_edgar_profile
    from sentinel.tools.finance import fetch_company_financials
    from sentinel.tools.osint import search_company_news
    from sentinel.tools.rag import build_compliance_brief
    from sentinel.tools.sanctions import screen_entity

    registry = ToolRegistry()

    registry.register(ToolSpec(
        name="screen_sanctions",
        summary="Screens an entity against the OFAC SDN list and OpenSanctions.",
        category=ToolCategory.SCREENING,
        fn=lambda subject_name, limit=5: screen_entity(subject_name, limit),
        classify=classify_sanctions,
        availability=_sanctions_data_present,
        fallback=lambda: {"status": "UNKNOWN", "matches": [], "error": "Screening did not run."},
        data_source="OFAC SDN (local CSV) + OpenSanctions API",
    ))

    registry.register(ToolSpec(
        name="fetch_sec_filings",
        summary="Resolves the entity in EDGAR and summarises its filing history.",
        category=ToolCategory.FILINGS,
        fn=lambda subject_name, ticker="": fetch_edgar_profile(subject_name, ticker),
        classify=classify_edgar,
        availability=_sec_identified,
        fallback=lambda: {"matched": False, "reason": "EDGAR lookup did not run.", "flags": [], "counts": {}},
        data_source="SEC EDGAR submissions API",
    ))

    registry.register(ToolSpec(
        name="fetch_company_financials",
        summary="Retrieves market capitalisation, leverage and profitability metrics.",
        category=ToolCategory.MARKET,
        # The LangChain tool object is called through its own protocol; the
        # registry hides that difference from every caller.
        fn=lambda ticker: fetch_company_financials.invoke({"ticker": ticker}),
        classify=classify_financials,
        fallback=lambda: {"error": "Market data lookup did not run."},
        data_source="Yahoo Finance (yfinance)",
    ))

    registry.register(ToolSpec(
        name="search_company_news",
        summary="Searches open-web reporting for adverse media on the entity.",
        category=ToolCategory.OSINT,
        fn=lambda query: search_company_news.invoke({"query": query}),
        classify=classify_news,
        availability=_tavily_configured,
        fallback=lambda: "",
        data_source="Tavily search API",
    ))

    registry.register(ToolSpec(
        name="build_compliance_brief",
        summary="Retrieves applicable policy passages and synthesises a control set.",
        category=ToolCategory.POLICY,
        fn=lambda query, subject="", context="": build_compliance_brief(query, subject, context),
        classify=classify_compliance_brief,
        availability=_corpus_indexed,
        fallback=lambda: {
            "retrieval": "unavailable",
            "summary": "The policy corpus was not queried.",
            "obligations": [],
            "sources": [],
            "error": "Compliance retrieval did not run.",
        },
        data_source="Chroma index over the framework corpus (all-MiniLM-L6-v2)",
    ))

    registry.register(ToolSpec(
        name="recall_entity_history",
        summary="Reads the audit ledger for earlier reviews of the same entity.",
        category=ToolCategory.MEMORY,
        fn=lambda subject, ticker="", exclude_id="": _recall(subject, ticker, exclude_id),
        classify=classify_history,
        availability=_ledger_reachable,
        fallback=lambda: {"count": 0, "reviews": [], "is_first_review": True,
                          "trend": "first_review", "trend_label": "First review"},
        data_source="SENTINEL investigation ledger (PostgreSQL)",
    ))

    registry.register(ToolSpec(
        name="assess_fraud_signals",
        summary="Scores fraud indicators and their convergence across categories.",
        category=ToolCategory.ANALYSIS,
        fn=_assess_fraud,
        classify=classify_fraud,
        fallback=lambda: {"score": 0.0, "signals": [], "patterns": [], "categories": []},
        data_source="Derived from filings, market data and reporting already collected",
    ))

    registry.register(ToolSpec(
        name="resolve_entity_network",
        summary="Resolves the entities connected to the subject from collected evidence.",
        category=ToolCategory.ANALYSIS,
        fn=_resolve_network,
        classify=classify_network,
        fallback=lambda: {"node_count": 0, "edge_count": 0, "flagged_count": 0,
                          "nodes": [], "edges": []},
        data_source="Derived from EDGAR records, watchlist entries and reporting",
    ))

    registry.register(ToolSpec(
        name="review_assessment",
        summary="Runs the critic's checks over the composed verdict.",
        category=ToolCategory.ANALYSIS,
        fn=_review_assessment,
        classify=classify_review,
        fallback=lambda: {"verdict": "", "challenges": []},
        data_source="Derived from the assessment, evidence pool and tool audit trail",
    ))

    return registry


# Thin adapters so the registry owns a uniform call signature while the services
# keep their own keyword-only interfaces.

def _recall(subject: str, ticker: str, exclude_id: str) -> Dict[str, Any]:
    from sentinel.services.memory import recall

    return recall(subject, ticker, exclude_id=exclude_id).to_dict()


def _assess_fraud(edgar: Any, financials: Any, news: Any, evidence: Any, research: Any = None) -> Dict[str, Any]:
    from sentinel.services.fraud import assess_fraud

    return assess_fraud(
        edgar=edgar, financials=financials, news=news, evidence=evidence, research=research,
    ).to_dict()


def _resolve_network(subject: str, ticker: str, edgar: Any, sanctions: Any,
                     news: Any, evidence: Any) -> Dict[str, Any]:
    from sentinel.services.knowledge_graph import build_graph

    return build_graph(
        subject=subject, ticker=ticker, edgar=edgar,
        sanctions=sanctions, news=news, evidence=evidence,
    ).to_dict()


def _review_assessment(assessment: Any, pool: Any, audit: Any,
                       plan: Any, revision: int = 0) -> Dict[str, Any]:
    # `audit` rather than `trail`: `invoke` reserves `trail` for the audit trail
    # it writes to, so a tool argument of that name would collide.
    from sentinel.services.critic import review

    return review(
        assessment, pool=pool, trail=audit, plan=plan, revision=revision,
    ).to_dict()


_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """The process-wide registry, built once."""
    global _registry
    if _registry is None:
        _registry = build_default_registry()
    return _registry
