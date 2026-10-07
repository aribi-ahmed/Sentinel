"""One-off migration of the audit ledger from SQLite to PostgreSQL.

The project started on a local SQLite file. Moving the system of record to
Postgres (objective M-09) would otherwise abandon the investigation history —
including the stored executive reports the ledger offers for download — so this
script copies it across.

It is idempotent: rows whose id already exists in the target are skipped, so a
re-run after a partial failure is safe.

    python scripts/migrate_sqlite_to_postgres.py            # migrate
    python scripts/migrate_sqlite_to_postgres.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from sentinel.config.settings import settings  # noqa: E402
from sentinel.database.models import Base, Investigation  # noqa: E402

SOURCE_URL = f"sqlite:///{BASE_DIR / 'sentinel_audit.db'}"

# Columns copied verbatim; `id` is handled separately as the identity key.
FIELDS = (
    "subject_name", "ticker", "status", "risk_level", "human_approved",
    "supervisor_reasoning", "final_report", "created_at", "updated_at",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would move, change nothing")
    args = parser.parse_args()

    target_url = settings.DATABASE_URL
    if target_url.startswith("sqlite"):
        print(f"Target DATABASE_URL is still SQLite ({target_url}). Point it at Postgres first.")
        return 1

    source_path = BASE_DIR / "sentinel_audit.db"
    if not source_path.is_file():
        print(f"No SQLite ledger at {source_path}; nothing to migrate.")
        return 0

    source = create_engine(SOURCE_URL, connect_args={"check_same_thread": False})
    target = create_engine(target_url)

    Base.metadata.create_all(bind=target)

    with Session(source) as src, Session(target) as dst:
        rows = src.execute(select(Investigation)).scalars().all()
        existing = set(dst.execute(select(Investigation.id)).scalars().all())

        pending = [row for row in rows if row.id not in existing]
        with_reports = sum(1 for row in pending if row.final_report)

        print(f"source     : {len(rows)} investigation(s) in SQLite")
        print(f"target     : {len(existing)} already present in Postgres")
        print(f"to migrate : {len(pending)} ({with_reports} carrying a stored report)")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
        if not pending:
            print("\nNothing to do.")
            return 0

        for row in pending:
            dst.add(Investigation(id=row.id, **{field: getattr(row, field) for field in FIELDS}))
        dst.commit()

        moved = dst.query(Investigation).count()
        print(f"\nMigrated. Postgres now holds {moved} investigation(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
