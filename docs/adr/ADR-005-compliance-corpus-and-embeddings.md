# ADR-005: A dedicated compliance corpus with local embeddings

**Status:** Accepted
**Date:** 2026-08-20
**Objectives:** M-05 (retrieval with citations)

## Context

The compliance agent was returning what looked like retrieved policy but was
actually scraped web text: fragmentary, carrying PDF running headers, and cited
to whatever URL a search engine had surfaced.

Three faults compounded:

1. **The retrieval path never ran.** `rag.py` required `OPENAI_API_KEY` — unset —
   and requested `text-embedding-3-small` (1536 dimensions). The existing store
   was built at **384** dimensions. Even with a key it could not have queried it.
2. **The corpus was the wrong corpus.** `ingest.py` indexes `./datasets`, which
   holds the OFAC SDN name lists — not policy prose. The three regulatory PDFs in
   `./docs` had never been indexed at all.
3. **So it silently fell through to Tavily**, and the UI faithfully displayed the
   result. The failure was invisible because every layer degraded quietly.

## Options considered

1. **Add an OpenAI key and re-embed everything at 1536 dimensions.** Works, costs
   money per re-index, and adds a hard dependency on a paid API for a project
   that is explicitly meant to run on free tiers.
2. **Re-point `rag.py` at the existing 384-dimension store.** No new corpus, but
   that store holds sanctions names — retrieving "board oversight duties" from a
   list of designated persons returns noise.
3. **Build a separate policy corpus with the same local embedding model already
   used elsewhere in the project.**

## Decision

**Option 3.**

* `ingest_compliance.py` indexes `./docs` into `chroma_compliance/`, collection
  `compliance_policy`: DOJ ECCP 2024, the SEC Enforcement Manual, NIST CSF 2.0 —
  **500 retrievable passages**.
* Embeddings are `all-MiniLM-L6-v2` running locally: free, no key, and the same
  model the rest of the project uses, so dimensions cannot drift apart again.
* Sanctions data is used for what it is actually good at — **screening a name**,
  not semantic retrieval — in `tools/sanctions.py`.

Ingestion repairs what PDF extraction breaks: hyphenation splits across lines,
justified-text artefacts (`T hird` → `Third`, `risk- based` → `risk-based`),
repeated running headers, and blocks that are citations rather than obligations.

**Retrieval alone was not enough.** Raw passages are fragmentary and not written
for this reader, so a synthesis pass turns them into named obligations with a
plain-English requirement and a reason it applies to this entity.

**Citations resolve by passage index, not by model-written name.** The first
implementation asked the model to name its source; it cited *"A Framework for
OFAC Compliance Commitments (2019)"* — a document title it had read inside a
footnote block, not the passage it used. Now the model returns the passage
number and the framework and page are resolved server-side, so a citation cannot
name a document that was not retrieved.

## Consequences

* **Positive.** M-05 is met with real citations: framework, page, and the verbatim
  passage available behind a disclosure so the synthesis stays auditable.
* **Positive.** No API key, no per-index cost, no dimension mismatch possible.
* **Negative.** A second vector store to maintain, and `ingest_compliance.py` must
  be re-run when `docs/` changes. The index is git-ignored (≈6 MB).
* **Negative.** Local embedding means the first ingest downloads the model
  (~90 MB) and takes about a minute.
* **Retained.** Tavily remains a secondary source when local retrieval returns
  nothing, and the UI states which path produced the result.
