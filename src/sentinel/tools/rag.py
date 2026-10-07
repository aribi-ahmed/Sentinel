"""Compliance policy retrieval and synthesis.

Retrieval alone produced the problem this module exists to solve: raw PDF and web
chunks arriving in the UI complete with running headers, hyphenation damage and
half-sentences. So the tool now does two things — it retrieves the passages, then
it has the model turn them into named obligations with a plain-English
requirement and a citation back to the framework and page it came from.

The vector store is built by `ingest_compliance.py` and embedded with a local
MiniLM model, so retrieval costs nothing and needs no API key.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from sentinel.config.settings import BASE_DIR
from sentinel.llm import AllProvidersFailed, ModelProfile, get_gateway

PERSIST_DIR = BASE_DIR / "chroma_compliance"
COLLECTION = "compliance_policy"
EMBED_MODEL = "all-MiniLM-L6-v2"
PASSAGES = 6
MAX_OBLIGATIONS = 4

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

try:
    from tavily import TavilyClient
    HAS_TAVILY = True
except ImportError:
    HAS_TAVILY = False

try:
    from langchain_community.vectorstores import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    HAS_VECTORSTORE = True
except ImportError:
    HAS_VECTORSTORE = False

_VECTORSTORE = None


def tidy(text: str) -> str:
    """Repairs the layout damage PDF and HTML extraction leaves in a passage."""
    text = (text or "").replace("�", "'")
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)
    text = re.sub(r"\b([B-HJ-Z]) ([a-z]{2,})", r"\1\2", text)
    text = re.sub(r"[•●▪]\s*", "\n• ", text)
    text = re.sub(r"(?<![.:;?!\n])\n(?![\n•])", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _get_vectorstore():
    """Opens the policy collection once and reuses it across invocations."""
    global _VECTORSTORE
    if _VECTORSTORE is not None:
        return _VECTORSTORE
    if not HAS_VECTORSTORE or not PERSIST_DIR.is_dir():
        return None
    try:
        _VECTORSTORE = Chroma(
            persist_directory=str(PERSIST_DIR),
            collection_name=COLLECTION,
            embedding_function=HuggingFaceEmbeddings(model_name=EMBED_MODEL),
        )
    except Exception:
        _VECTORSTORE = None
    return _VECTORSTORE


def _retrieve_passages(query: str) -> List[Dict[str, Any]]:
    """Pulls the most relevant policy passages from the local framework index."""
    store = _get_vectorstore()
    if store is None:
        return []

    try:
        hits = store.similarity_search_with_relevance_scores(query, k=PASSAGES)
    except Exception:
        return []

    passages, seen = [], set()
    for document, score in hits:
        content = tidy(document.page_content)
        fingerprint = content[:160]
        if not content or fingerprint in seen:
            continue
        seen.add(fingerprint)
        metadata = document.metadata or {}
        passages.append({
            "framework": metadata.get("framework", "Internal policy"),
            "page": metadata.get("page"),
            "relevance": round(float(score), 3),
            "excerpt": content,
        })
    return passages


def _retrieve_from_web(query: str) -> List[Dict[str, Any]]:
    """Secondary source for when the local frameworks return nothing usable."""
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not HAS_TAVILY or not tavily_key:
        return []
    try:
        response = TavilyClient(api_key=tavily_key).search(
            query=f"{query} regulatory compliance obligation corporate governance standard",
            search_depth="advanced",
            max_results=4,
        )
    except Exception:
        return []

    return [
        {
            "framework": item.get("title") or "External regulatory source",
            "page": None,
            "url": item.get("url", ""),
            "relevance": round(float(item.get("score", 0) or 0), 3),
            "excerpt": tidy(item.get("content", "")),
        }
        for item in response.get("results", [])
        if item.get("content")
    ]


SYNTHESIS_PROMPT = """You are a compliance counsel writing the controls section of a risk file on
{subject}.

Below are verbatim passages retrieved from regulatory frameworks. They are raw
extracts: fragmentary, full of cross-references, and not written for this
reader. Your job is to turn them into a short, organised set of the obligations
that actually bear on this entity.

Rules:
- Write in your own words. Never copy a sentence from the passage.
- State each obligation as something the entity must DO, in plain English.
- Only include an obligation the passages actually support. Do not invent
  requirements, and do not pad the list — two well-grounded obligations beat
  four vague ones.
- Explain the relevance to THIS entity, given what the investigation found.
- Cite the passage you used by its number. Passages sometimes quote or list OTHER
  documents; those are not your source. The source is the numbered passage
  itself, so "source" must always be one of the numbers below.

Return ONLY a JSON object, no prose or code fences:

{{
  "obligations": [
    {{
      "control": "<short control name, 2-5 words>",
      "requirement": "<one or two plain-English sentences on what is required>",
      "applies_because": "<one sentence tying it to this entity>",
      "source": <passage number>,
      "severity": "<core|standard|advisory>"
    }}
  ],
  "summary": "<two sentences on the compliance posture these controls imply>"
}}

Return at most {limit} obligations.

=== ENTITY CONTEXT ===
{context}

=== RETRIEVED POLICY PASSAGES ===
{passages}
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
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


def _synthesise(subject: str, context: str, passages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Has the model turn raw passages into named, cited obligations."""
    rendered = "\n\n".join(
        f"[{index + 1}] {passage['framework']}"
        + (f", page {passage['page']}" if passage.get("page") else "")
        + f"\n{passage['excerpt'][:1400]}"
        for index, passage in enumerate(passages)
    )

    prompt = SYNTHESIS_PROMPT.format(
        subject=subject,
        limit=MAX_OBLIGATIONS,
        context=context or subject,
        passages=rendered,
    )

    try:
        # Turning retrieved passages into named obligations is extraction, so it
        # asks for that profile rather than the reasoning model.
        reply = get_gateway().complete(
            prompt,
            profile=ModelProfile.EXTRACTION,
            temperature=0.15,
            caller="compliance_analyst",
        )
    except AllProvidersFailed as exc:
        return {"obligations": [], "summary": "", "error": str(exc)}

    payload = _extract_json(reply.text)
    if not payload:
        return {"obligations": [], "summary": "", "error": "Synthesis reply was not valid JSON."}

    obligations = []
    for item in payload.get("obligations", [])[:MAX_OBLIGATIONS]:
        if not isinstance(item, dict):
            continue
        requirement = str(item.get("requirement") or "").strip()
        if not requirement:
            continue

        # Resolve the citation from the passage index rather than trusting a
        # model-written framework name: passages quote other documents, and the
        # model will otherwise cite whatever title it saw in the text.
        try:
            position = int(item.get("source", 1)) - 1
        except (TypeError, ValueError):
            position = 0
        cited = passages[position] if 0 <= position < len(passages) else passages[0]

        severity = str(item.get("severity") or "standard").strip().lower()
        obligations.append({
            "control": str(item.get("control") or "Compliance control").strip(),
            "requirement": requirement,
            "applies_because": str(item.get("applies_because") or "").strip(),
            "framework": cited["framework"],
            "page": cited.get("page"),
            "url": cited.get("url", ""),
            "excerpt": cited["excerpt"][:600],
            "severity": severity if severity in {"core", "standard", "advisory"} else "standard",
        })

    return {
        "obligations": obligations,
        "summary": str(payload.get("summary") or "").strip(),
        "error": None,
    }


def build_compliance_brief(query: str, subject: str = "", context: str = "") -> Dict[str, Any]:
    """Retrieves the applicable policy and returns a synthesised control set."""
    subject = subject or query

    passages = _retrieve_passages(query)
    retrieval = "frameworks"
    if not passages:
        passages = _retrieve_from_web(query)
        retrieval = "web" if passages else "unavailable"

    if not passages:
        return {
            "retrieval": "unavailable",
            "summary": (
                "No policy corpus is available. Run `python ingest_compliance.py` to index "
                "the frameworks in ./docs before relying on this section."
            ),
            "obligations": [],
            "sources": [],
            "error": "No compliance passages could be retrieved.",
        }

    synthesis = _synthesise(subject, context, passages)

    return {
        "retrieval": retrieval,
        "summary": synthesis["summary"],
        "obligations": synthesis["obligations"],
        # Provenance for every synthesised claim, shown behind a disclosure in
        # the UI so the verdict stays auditable.
        "sources": [
            {
                "framework": passage["framework"],
                "page": passage.get("page"),
                "url": passage.get("url", ""),
                "relevance": passage.get("relevance"),
                "excerpt": passage["excerpt"][:900],
            }
            for passage in passages
        ],
        "error": synthesis["error"],
    }


@tool
def query_compliance_rag(query: str) -> Dict[str, Any]:
    """Retrieves applicable compliance obligations from the policy frameworks."""
    return build_compliance_brief(query)


# Alias export so both names work across graph nodes and agent tools.
query_compliance_policy = query_compliance_rag
