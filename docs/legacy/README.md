# Retired code

Kept as text, outside the import path, so the history of the design is legible
without the modules being live.

## `app.py.txt` — standalone RAG chat service (retired 2026-08-20)

The first prototype: a single FastAPI file that answered questions over a Chroma
index with one direct `ChatGroq` call. It was superseded by the multi-agent
graph, but was never removed, and it caused three concrete problems:

1. **It broke objective M-01.** It imported `langchain_groq` directly, so the
   claim that providers are swappable by configuration was untrue for part of
   the shipped surface. `tests/unit/test_architecture.py` now fails on exactly
   this, and that test is what flagged it.
2. **It contradicted the documented architecture.** A reviewer opening the
   repository found two competing entry points and two different contracts on
   `POST /investigations`.
3. **It could not run.** It read `./chroma_db_free`, a directory that no longer
   exists; the compliance corpus moved to `./chroma_compliance` and is built by
   `ingest_compliance.py`.

Recover the original with `git show HEAD:app.py` if any of it is ever wanted.

## `compliance_agent.py.txt`, `financial_agent.py.txt` — unused agent wrappers (retired 2026-08-22)

Two thin wrappers from the prototype, superseded by the specialist nodes in
`graph/nodes.py`. Nothing imported them, and `compliance_agent.py` had stopped
being importable at all: it referenced `query_compliance_policy`, a function
that no longer exists in `tools/rag.py` (it is `query_compliance_rag`). The
module would have raised `ImportError` on first use.

They were found by the M-04 architecture test, which requires every tool call to
go through the registry — these two were the only modules still importing a tool
module directly. The lesson is the same one as `app.py`: dead code is not inert.
It contradicts the documented design, it breaks the boundaries the tests are
meant to protect, and it rots silently because nothing exercises it.
