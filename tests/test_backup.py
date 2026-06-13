"""Tests for the database backup."""
import sqlite3
import pytest

from pipeline.backup import backup_db


def test_backup_creates_consistent_copy(temp_db, tmp_path):
    dest = tmp_path / "bk"
    out, pruned = backup_db(dest=dest, db_path=temp_db)
    assert out.exists()
    assert out.parent == dest
    # the copy is a valid sqlite db with the same tables
    with sqlite3.connect(out) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "borrower" in tables and "cma_statement" in tables


def test_backup_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_db(dest=tmp_path / "bk", db_path=tmp_path / "nope.sqlite")


def test_backup_prunes_to_keep(temp_db, tmp_path):
    dest = tmp_path / "bk"
    import time
    for _ in range(4):
        backup_db(dest=dest, db_path=temp_db, keep=2)
        time.sleep(1.05)            # timestamps are per-second
    remaining = list(dest.glob("cma_*.sqlite"))
    assert len(remaining) == 2
