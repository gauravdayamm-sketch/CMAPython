"""Inspect the SQLite database: tables, schemas, row counts."""
import sqlite3
import pathlib

DB = pathlib.Path(__file__).resolve().parents[1] / "db" / "cma.sqlite"

conn = sqlite3.connect(DB)
tables = conn.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()

for name, sql in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
    print(f"=== {name} ({count} rows) ===")
    print(sql)
    print()

conn.close()
