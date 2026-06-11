"""Live validation: fetch + classify news for the ingested real borrowers."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sqlite3
from data.news_intel import refresh_borrower_news, get_news, DB

NAMES = [
    "ARROWIN METALTECH (INDIA) PRIVATE LIMITED",
    "RAMDEV SILICATE",
    "Wintas Textiles",
    "BHT FORGE INDIA PRIVATE LIMITED",
]

for name in NAMES:
    r = refresh_borrower_news(name)
    print(f"{name[:38]:<40} fetched={r['fetched']:>2} new={r['new']:>2} "
          f"adverse={r['adverse']}  {r['error'][:50]}")

print("\nSample cached headlines:")
with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT b.name, n.title, n.classification, n.classified_by
        FROM news_cache n JOIN borrower b ON b.borrower_id = n.borrower_id
        ORDER BY n.fetched_at DESC LIMIT 8
    """).fetchall()
for row in rows:
    print(f"  [{row['classification']}] ({row['classified_by']}) "
          f"{row['name'][:20]}: {row['title'][:80]}")
