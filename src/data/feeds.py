"""
Data Feeds Module
Three live data sources:
  1. NSE/BSE equity data     — via jugaad-data
  2. RBI regulatory updates  — via official RSS feed
  3. Macro indicators        — cached in SQLite
"""
import sqlite3
import pathlib
import datetime
import feedparser
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


# ── 1. NSE Equity Data ────────────────────────────────────────────────────────

def fetch_nse_quote(symbol: str):
    """
    Fetch latest NSE quote for a stock symbol or index (e.g. "NIFTY 50").
    Returns dict with price, change, volume or error message.
    """
    try:
        from jugaad_data.nse import NSELive
        nse = NSELive()

        # Indices go through the live_index endpoint — stock_quote only
        # accepts equity symbols.
        if symbol.upper().startswith(("NIFTY", "BANKNIFTY")):
            data = nse.live_index(symbol)
            rows = data.get("data") or [{}]
            idx  = rows[0]
            return {
                "symbol":      symbol,
                "last_price":  idx.get("lastPrice", 0),
                "change":      idx.get("change", 0),
                "pct_change":  idx.get("pChange", 0),
                "high":        idx.get("dayHigh", 0),
                "low":         idx.get("dayLow", 0),
                "52w_high":    idx.get("yearHigh", 0),
                "52w_low":     idx.get("yearLow", 0),
                "fetched_at":  datetime.datetime.now().isoformat(),
                "error":       None,
            }

        quote = nse.stock_quote(symbol)
        price_info = quote.get("priceInfo", {})

        return {
            "symbol":      symbol,
            "last_price":  price_info.get("lastPrice", 0),
            "change":      price_info.get("change", 0),
            "pct_change":  price_info.get("pChange", 0),
            "high":        price_info.get("intraDayHighLow", {}).get("max", 0),
            "low":         price_info.get("intraDayHighLow", {}).get("min", 0),
            "52w_high":    price_info.get("weekHighLow", {}).get("max", 0),
            "52w_low":     price_info.get("weekHighLow", {}).get("min", 0),
            "fetched_at":  datetime.datetime.now().isoformat(),
            "error":       None,
        }

    except Exception as e:
        return {
            "symbol":  symbol,
            "error":   str(e),
        }


def fetch_nse_history(symbol: str, days: int = 30):
    """
    Fetch historical daily data for a symbol.
    Returns pandas DataFrame with date, open, high, low, close, volume.
    """
    try:
        from jugaad_data.nse import stock_df
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        df    = stock_df(symbol=symbol, from_date=start,
                         to_date=end, series="EQ")
        return df, None
    except Exception as e:
        return None, str(e)


def save_macro_indicator(name: str, value: float, source: str = "NSE"):
    """Save a macro indicator value to SQLite."""
    try:
        with sqlite3.connect(DB) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO macro_indicators
                (indicator_name, value_date, value, source)
                VALUES (?, ?, ?, ?)
            """, (name, datetime.date.today().isoformat(), value, source))
    except Exception as e:
        print(f"  DB save failed for {name}: {e}")


# ── 2. RBI Regulatory Feed ───────────────────────────────────────────────────

RBI_FEEDS = {
    "notifications":  "https://rbi.org.in/notifications_rss.xml",
    "press_releases": "https://rbi.org.in/pressreleases_rss.xml",
    "circulars":      "https://rbi.org.in/Scripts/Rss.aspx?Id=0",
}

def fetch_rbi_updates(kind: str = "notifications", since_days: int = 30):
    """
    Fetch RBI notifications via official RSS feed.
    Returns list of dicts with title, link, published, summary.
    """
    feed_url = RBI_FEEDS.get(kind, RBI_FEEDS["notifications"])

    try:
        feed    = feedparser.parse(feed_url)
        cutoff  = datetime.datetime.now() - datetime.timedelta(days=since_days)
        items   = []

        for entry in feed.entries:
            # Parse published date
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime.datetime(*entry.published_parsed[:6])
            else:
                pub = datetime.datetime.now()

            if pub < cutoff:
                continue

            item = {
                "title":     entry.get("title", "No title"),
                "link":      entry.get("link", ""),
                "published": pub.isoformat(),
                "summary":   entry.get("summary", "")[:500],
            }
            items.append(item)

        return items, None

    except Exception as e:
        return [], str(e)


def save_rbi_to_db(items: list):
    """Cache RBI notifications in SQLite. Returns count of NEW rows only."""
    saved = 0
    with sqlite3.connect(DB) as conn:
        for item in items:
            try:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO rbi_cache
                    (link, title, published, summary)
                    VALUES (?, ?, ?, ?)
                """, (
                    item["link"],
                    item["title"],
                    item["published"],
                    item["summary"],
                ))
                # rowcount is 0 when OR IGNORE skipped a duplicate link
                saved += cur.rowcount
            except Exception:
                pass
    return saved


def get_rbi_cached(limit: int = 10):
    """Retrieve latest RBI notifications from SQLite cache."""
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM rbi_cache
            ORDER BY published DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── 3. Daily refresh ──────────────────────────────────────────────────────────

def daily_refresh():
    """
    Run all data fetches and cache results.
    Called by APScheduler at 2 AM daily.
    """
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] Daily refresh starting...")

    # RBI notifications
    print("  Fetching RBI notifications...")
    items, err = fetch_rbi_updates("notifications", since_days=7)
    if err:
        print(f"  RBI feed error: {err}")
    else:
        saved = save_rbi_to_db(items)
        print(f"  RBI: {len(items)} items fetched, {saved} new saved to DB")

    # RBI press releases
    print("  Fetching RBI press releases...")
    items2, err2 = fetch_rbi_updates("press_releases", since_days=7)
    if err2:
        print(f"  RBI press releases error: {err2}")
    else:
        saved2 = save_rbi_to_db(items2)
        print(f"  RBI press releases: {len(items2)} items, {saved2} new saved")

    # Sample NSE indices as macro indicators
    print("  Fetching NSE market data...")
    for symbol in ["NIFTY 50", "NIFTY BANK"]:
        quote = fetch_nse_quote(symbol)
        if not quote.get("error"):
            save_macro_indicator(
                name=symbol,
                value=quote["last_price"],
                source="NSE"
            )
            print(f"  {symbol}: {quote['last_price']}")
        else:
            print(f"  {symbol}: {quote['error']}")

    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] Daily refresh complete.")


# ── 4. Scheduler setup ────────────────────────────────────────────────────────

def start_scheduler():
    """
    Start APScheduler background job for daily 2 AM refresh.
    Returns the scheduler instance.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        daily_refresh,
        CronTrigger(hour=2, minute=0),
        id="daily_refresh",
        replace_existing=True,
    )
    scheduler.start()
    print(f"Scheduler started — daily refresh at 02:00 IST")
    return scheduler


# ── 5. Summary for Excel ──────────────────────────────────────────────────────

def get_feed_summary():
    """
    Returns a flat dict for excel_io.py output.
    """
    # Get cached RBI items
    rbi_items = get_rbi_cached(limit=5)

    # Get latest macro indicators
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        macro_rows = conn.execute("""
            SELECT indicator_name, value, value_date, source
            FROM macro_indicators
            ORDER BY value_date DESC
            LIMIT 10
        """).fetchall()

    return {
        "rbi_count":   len(rbi_items),
        "rbi_items":   rbi_items,
        "macro_count": len(macro_rows),
        "macro_rows":  [dict(r) for r in macro_rows],
        "last_refresh": datetime.datetime.now().isoformat(),
    }