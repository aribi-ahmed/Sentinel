# ADR-008: Retire the prototype entry point

**Status:** Accepted
**Date:** 2026-08-20
**Objectives:** M-01 (provider boundary); rubric — code quality, documentation

## Context

`app.py` was the project's first prototype: a single-file FastAPI service that
answered questions over a Chroma index with one direct `ChatGroq` call. The
multi-agent graph superseded it, but the file stayed.

It was found by `tests/unit/test_architecture.py` on the first run after the scan
was widened past `src/`:

```
AssertionError: Provider SDKs must only be imported by the LLM gateway,
otherwise the provider-agnostic boundary is fiction.
Leaks: {'app.py': ['langchain_groq']}
```

Three concrete problems, not merely untidiness:

1. **It broke M-01.** The claim that providers are swappable by configuration was
   untrue of part of the shipped surface.
2. **It contradicted the documented architecture.** A reviewer opening the
   repository found two entry points and two different contracts on
   `POST /investigations`.
3. **It could not run.** It read `./chroma_db_free`, a directory that no longer
   exists; the compliance corpus moved to `./chroma_compliance`
   ([ADR-005](ADR-005-compliance-corpus-and-embeddings.md)).

## Options considered

1. **Leave it and exempt it from the test.** Dishonest: the exemption would exist
   solely to make a failing rule pass.
2. **Refactor it onto the gateway.** Real work to preserve a service nothing calls
   and whose data store is gone.
3. **Retire it, keeping the code readable in `docs/legacy/`.**

## Decision

**Option 3.** Moved to `docs/legacy/app.py.txt` — outside the import path, so it
cannot be run or imported, but still readable — with a note recording why. Removed
from git tracking, so the original stays recoverable via `git show HEAD:app.py`.

The architecture test's exemption now names the two adapter files individually
rather than a directory, so adding a file to `llm/` cannot silently widen the hole.

## Consequences

* **Positive.** M-01's boundary is true across the whole repository, and enforced.
* **Positive.** One entry point, matching the architecture document.
* **Negative.** The prototype is no longer runnable. Deliberate: it had not been
  runnable for some time, and appearing to work is worse than being absent.
* **Note.** This is the first defect the architecture test caught by itself, which
  is the argument for having written it.
