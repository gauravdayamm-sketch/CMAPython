"""
Create or migrate the CMA SQLite database.

Idempotent: safe to run on a fresh clone (creates all tables) or on an
existing database (applies the schema additively and deduplicates
anomaly_result before adding its unique index).

Usage:  python scripts/init_db.py
"""
import sqlite3
import pathlib

ROOT   = pathlib.Path(__file__).resolve().parents[1]
DB     = ROOT / "db" / "cma.sqlite"
SCHEMA = ROOT / "db" / "schema.sql"


def main():
    DB.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB) as conn:
        # The unique index on anomaly_result cannot be created while
        # historical duplicate rows exist — keep only the latest run
        # per (borrower_id, detector).
        has_anomaly = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='anomaly_result'"
        ).fetchone()
        if has_anomaly:
            removed = conn.execute("""
                DELETE FROM anomaly_result
                WHERE anom_id NOT IN (
                    SELECT MAX(anom_id) FROM anomaly_result
                    GROUP BY borrower_id, detector
                )
            """).rowcount
            if removed:
                print(f"Deduplicated anomaly_result: removed {removed} stale rows")

        conn.executescript(SCHEMA.read_text(encoding="utf-8"))

        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]

    print(f"Database ready: {DB}")
    print(f"Tables: {', '.join(tables)}")


if __name__ == "__main__":
    main()
