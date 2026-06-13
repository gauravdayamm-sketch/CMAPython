"""
Database backup — a consistent copy of db\\cma.sqlite (every ingested
borrower, registry/filing finding, news, manual index, Probe cache).

Uses SQLite's online backup API, so it is safe to run even while the tool
is open. Defaults to a CMA_Backups folder on OneDrive when available, so
the copy syncs off the laptop automatically; otherwise a local backups\\.
"""
import datetime
import os
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


def default_dest():
    """OneDrive\\CMA_Backups if OneDrive is configured, else .\\backups."""
    for var in ("OneDriveCommercial", "OneDrive", "OneDriveConsumer"):
        base = os.environ.get(var)
        if base and pathlib.Path(base).exists():
            return pathlib.Path(base) / "CMA_Backups"
    return ROOT / "backups"


def backup_db(dest=None, db_path=None, keep=14):
    """Write a timestamped consistent copy. Returns (path, list-pruned)."""
    src = pathlib.Path(db_path or DB)
    if not src.exists():
        raise FileNotFoundError(f"No database at {src} — nothing to back up.")

    dest_dir = pathlib.Path(dest) if dest else default_dest()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = dest_dir / f"cma_{stamp}.sqlite"

    # NB: `with sqlite3.connect(...)` commits but does NOT close the
    # connection — on Windows the open handle would block pruning. Close
    # both explicitly.
    s = sqlite3.connect(src)
    d = sqlite3.connect(out)
    try:
        s.backup(d)                         # online backup → consistent copy
    finally:
        d.close()
        s.close()

    # Prune: keep the most recent `keep` backups in this folder
    pruned = []
    if keep and keep > 0:
        existing = sorted(dest_dir.glob("cma_*.sqlite"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        for old in existing[keep:]:
            old.unlink()
            pruned.append(old.name)
    return out, pruned
