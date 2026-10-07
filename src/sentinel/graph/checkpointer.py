"""Checkpoint persistence for the investigation graph.

LangGraph saves the full graph state after every super-step, keyed by a thread
(one thread per investigation). Where those snapshots live decides whether the
platform can honour two of its objectives:

* **M-10 — resumability.** With the in-memory saver, restarting the API orphans
  every in-flight investigation: the human approval gate can never be resumed
  because the state it would resume from died with the process. Writing
  checkpoints to Postgres makes a run survive a crash, a deploy, or a reviewer
  who takes three days to decide.
* **M-06 — the human gate.** An interrupt is only useful if the pause can
  outlive the process that created it.

The saver degrades to memory when Postgres is unreachable rather than refusing
to start, so a developer without Docker can still run the graph — but the
degradation is reported, never silent, because a system that quietly stops
persisting is worse than one that fails loudly.
"""

from __future__ import annotations

import atexit
import logging
from dataclasses import dataclass
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver

from sentinel.config.settings import settings

logger = logging.getLogger(__name__)

# psycopg speaks plain `postgresql://`; the `+psycopg` suffix is SQLAlchemy's
# dialect selector and is not part of a libpq connection string.
_SQLALCHEMY_DIALECT_SUFFIXES = ("+psycopg", "+psycopg2", "+asyncpg")


@dataclass(frozen=True)
class CheckpointBackend:
    """The checkpointer plus how it was obtained, so the API can report it."""

    saver: Any
    name: str
    durable: bool
    detail: str
    pool: Any = None

    def close(self) -> None:
        """Releases the connection pool on shutdown.

        Without this the pool's finaliser tries to join its worker threads
        during interpreter shutdown, which Python 3.14 refuses; the API would
        log a PythonFinalizationError on every exit.
        """
        if self.pool is None:
            return
        try:
            self.pool.close()
        except Exception as exc:  # shutdown must never raise
            logger.debug("Checkpoint pool close failed: %s", exc)


def to_libpq_dsn(url: str) -> str:
    """Strips the SQLAlchemy dialect suffix from a database URL."""
    for suffix in _SQLALCHEMY_DIALECT_SUFFIXES:
        url = url.replace(suffix, "", 1)
    return url


def _memory_backend(detail: str) -> CheckpointBackend:
    return CheckpointBackend(saver=MemorySaver(), name="memory", durable=False, detail=detail)


def build_checkpointer(database_url: Optional[str] = None) -> CheckpointBackend:
    """Builds the durable checkpointer, falling back to memory if unavailable."""
    url = database_url or getattr(settings, "DATABASE_URL", "") or ""

    if not url.startswith("postgres"):
        return _memory_backend(
            "DATABASE_URL is not a Postgres URL, so checkpoints are in-process "
            "and will not survive a restart."
        )

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        return _memory_backend(f"Postgres checkpoint packages are not installed ({exc}).")

    try:
        # PostgresSaver requires autocommit and dict rows; `prepare_threshold=0`
        # keeps it working through connection poolers that reject prepared
        # statements. `open=True` surfaces a bad DSN here rather than on the
        # first investigation.
        pool = ConnectionPool(
            conninfo=to_libpq_dsn(url),
            min_size=1,
            max_size=int(getattr(settings, "DB_POOL_SIZE", 10)),
            timeout=10.0,
            open=True,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        saver = PostgresSaver(pool)
        # Creates the checkpoint tables if they are not there yet; idempotent.
        saver.setup()
    except Exception as exc:
        logger.warning("Durable checkpointing unavailable, falling back to memory: %s", exc)
        return _memory_backend(f"Postgres was unreachable ({exc.__class__.__name__}: {exc}).")

    logger.info("Checkpointing to Postgres; investigations survive a restart.")
    backend = CheckpointBackend(
        saver=saver,
        name="postgres",
        durable=True,
        detail="Checkpoints are written to Postgres; interrupted runs resume after a restart.",
        pool=pool,
    )
    # The API closes the pool through its lifespan; this covers scripts and the
    # test suite, which import the graph without one.
    atexit.register(backend.close)
    return backend
