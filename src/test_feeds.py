"""Test data feeds module."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Windows consoles default to cp1252, which cannot print symbols like ₹
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from data.feeds import (
    fetch_rbi_updates, save_rbi_to_db,
    get_rbi_cached, fetch_nse_quote, daily_refresh
)

print("=" * 60)
print("TEST 1: RBI Notifications Feed")
print("=" * 60)
items, err = fetch_rbi_updates("notifications", since_days=30)
if err:
    print(f"  Error: {err}")
else:
    print(f"  Fetched {len(items)} notifications from past 30 days")
    if items:
        print(f"  Latest: {items[0]['title'][:70]}")
        print(f"  Date:   {items[0]['published'][:10]}")
        saved = save_rbi_to_db(items)
        print(f"  Saved {saved} new items to SQLite")

print()
print("=" * 60)
print("TEST 2: RBI Cache Retrieval")
print("=" * 60)
cached = get_rbi_cached(limit=3)
print(f"  Items in cache: {len(cached)}")
for item in cached:
    print(f"  - {item['title'][:65]}")
    print(f"    {item['published'][:10]}")

print()
print("=" * 60)
print("TEST 3: NSE Quote")
print("=" * 60)
print("  Trying RELIANCE...")
q = fetch_nse_quote("RELIANCE")
if q.get("error"):
    print(f"  Error: {q['error']}")
    print("  (NSE may block automated requests — this is expected)")
else:
    print(f"  Last Price: {q['last_price']}")
    print(f"  Change:     {q['change']} ({q['pct_change']}%)")
    print(f"  52W High:   {q['52w_high']}")
    print(f"  52W Low:    {q['52w_low']}")

print()
print("=" * 60)
print("TEST 4: Manual Daily Refresh")
print("=" * 60)
daily_refresh()