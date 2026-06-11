"""Unit tests for the data feeds module (network fully mocked)."""
import sqlite3
import time
import pytest

from data import feeds


ITEMS = [
    {"link": "https://rbi.org.in/n1", "title": "Notification 1",
     "published": "2026-06-01T10:00:00", "summary": "First"},
    {"link": "https://rbi.org.in/n2", "title": "Notification 2",
     "published": "2026-06-05T10:00:00", "summary": "Second"},
]


def test_save_rbi_counts_only_new_rows(temp_db):
    """Regression: duplicates skipped by INSERT OR IGNORE must not be
    counted as saved."""
    assert feeds.save_rbi_to_db(ITEMS) == 2
    assert feeds.save_rbi_to_db(ITEMS) == 0      # same items again
    extra = ITEMS + [{"link": "https://rbi.org.in/n3", "title": "Notification 3",
                      "published": "2026-06-07T10:00:00", "summary": "Third"}]
    assert feeds.save_rbi_to_db(extra) == 1      # only the new one

    with sqlite3.connect(temp_db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM rbi_cache").fetchone()[0]
    assert n == 3


def test_get_rbi_cached_orders_newest_first(temp_db):
    feeds.save_rbi_to_db(ITEMS)
    cached = feeds.get_rbi_cached(limit=10)
    assert [c["link"] for c in cached] == ["https://rbi.org.in/n2",
                                           "https://rbi.org.in/n1"]


class _FakeEntry(dict):
    """feedparser entries support both dict and attribute access."""
    def __init__(self, title, link, published_parsed):
        super().__init__(title=title, link=link, summary="s")
        if published_parsed is not None:
            self.published_parsed = published_parsed


def test_fetch_rbi_filters_by_cutoff(monkeypatch):
    recent = time.localtime(time.time() - 5 * 86400)       # 5 days ago
    ancient = time.localtime(time.time() - 90 * 86400)     # 90 days ago

    class FakeFeed:
        entries = [
            _FakeEntry("Recent", "https://rbi.org.in/recent", recent),
            _FakeEntry("Ancient", "https://rbi.org.in/ancient", ancient),
        ]

    monkeypatch.setattr(feeds.feedparser, "parse", lambda url: FakeFeed())
    items, err = feeds.fetch_rbi_updates("notifications", since_days=30)
    assert err is None
    assert [i["title"] for i in items] == ["Recent"]


def test_fetch_rbi_returns_error_string_on_failure(monkeypatch):
    def boom(url):
        raise ConnectionError("network down")
    monkeypatch.setattr(feeds.feedparser, "parse", boom)
    items, err = feeds.fetch_rbi_updates("notifications")
    assert items == []
    assert "network down" in err


def test_save_macro_indicator_upserts(temp_db):
    feeds.save_macro_indicator("NIFTY 50", 25000.0)
    feeds.save_macro_indicator("NIFTY 50", 25100.0)   # same day -> replace
    with sqlite3.connect(temp_db) as conn:
        rows = conn.execute(
            "SELECT value FROM macro_indicators WHERE indicator_name='NIFTY 50'"
        ).fetchall()
    assert rows == [(25100.0,)]
