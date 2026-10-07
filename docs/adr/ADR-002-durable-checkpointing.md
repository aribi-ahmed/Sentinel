# ADR-002: Durable checkpointing in PostgreSQL

**Status:** Accepted
**Date:** 2026-08-20
**Objectives:** M-09 (persistence), M-10 (checkpointing), M-06 (human gate)

## Context

The graph compiled with `MemorySaver()`, LangGraph's in-process checkpointer.
That choice is invisible until the process restarts, and then it is total: the
checkpoint series for every in-flight investigation dies with the interpreter.

Two objectives depended on it and neither held:

* **M-10** is accepted by *"kill the process mid-run; resume completes the
  investigation."* It did not — a restarted API returned empty state for a paused
  run, so the approval gate could never be resumed.
* **M-06's** human gate is only useful if the pause can outlive the process that
  created it. A reviewer taking a day to decide was a reviewer whose
  investigation had already been lost to the next deploy.

We hit this in practice: restarting `uvicorn` during development orphaned every
investigation that was waiting at the gate.

The system of record had the same problem in a slower form — a SQLite file, while
`docker-compose.yml` declared a PostgreSQL service that nothing connected to.

## Options considered

1. **Keep `MemorySaver`, document the limitation.** Free, and honest, but leaves
   two mandatory objectives unmet and makes the most persuasive demo impossible.
2. **File-backed checkpointer (SQLite).** Survives restarts. But it keeps two
   storage engines in play, and SQLite's write locking is a poor fit for four
   specialists checkpointing concurrently.
3. **`PostgresSaver` against the Postgres already in compose.** Checkpoints and
   business records share one engine, one backup story, one connection pool.

## Decision

**Option 3.** `langgraph-checkpoint-postgres` with a pooled `psycopg` connection,
built in `sentinel/graph/checkpointer.py`.

Details that mattered:

* `autocommit=True`, `row_factory=dict_row` and `prepare_threshold=0` — the first
  two are required by `PostgresSaver`, the third keeps it working through a
  connection pooler that rejects prepared statements.
* `open=True` on the pool, so a bad DSN fails at startup rather than on the first
  investigation.
* **Graceful degradation.** If Postgres is unreachable the gateway falls back to
  `MemorySaver` and *says so* — a developer without Docker can still run the
  graph, but nobody is misled into thinking their run is durable. The API reports
  the live backend at `GET /system`, and the console shows it in the masthead.
* Host ports are parameterised (`POSTGRES_PORT`), because another project on this
  machine already owned 5432 and silently prevented the compose container from
  publishing.

## Consequences

* **Positive.** M-09 and M-10 pass their stated acceptance tests. The
  crash-and-resume demonstration §11 rewards is now possible: kill the API, restart
  it, approve a pending investigation, watch it finish.
* **Positive.** Checkpoint history is queryable SQL, which makes the "time travel"
  debugging of §6.5 available.
* **Negative.** Postgres is now required for full function. Mitigated by the
  degradation path and by compose making it one command.
* **Negative.** The pool holds worker threads, and Python 3.14 refuses to join
  threads at interpreter shutdown. Resolved by closing the pool from the FastAPI
  `lifespan` and registering an `atexit` handler for scripts.
* **Migration.** 169 existing investigations, including 47 with stored reports,
  were carried across by `scripts/migrate_sqlite_to_postgres.py` (idempotent,
  `--dry-run` supported) rather than abandoned.

## Verification

`tests/unit/test_repositories.py` runs the repository contract against real
Postgres. The resume behaviour was verified by hand end to end, and the sequence
is written up in the README as a demo script.
