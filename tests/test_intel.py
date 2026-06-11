"""Tests for news intelligence + registry due diligence (network/LLM mocked)."""
import sqlite3
import pytest

from data import news_intel, registry_checks
from data.news_intel import (
    classify_headline, refresh_borrower_news, count_adverse,
)
from data.registry_checks import (
    record_check, get_checks, due_diligence_gaps, REGISTRIES,
)
from analytics.ews import run_full_ews
from ingest.demo_fixture import BORROWER


# ── Headline classification (keyword screen, no LLM) ─────────────────────────

def test_adverse_keywords_classified():
    for title in ("Acme Ltd faces NCLT insolvency plea from lender",
                  "ED raid at Acme premises over money laundering",
                  "Bank declares Acme account NPA after default"):
        v = classify_headline(title, use_llm=False)
        assert v["classification"] == "ADVERSE", title


def test_neutral_headline():
    v = classify_headline("Acme Ltd opens new plant in Pune", use_llm=False)
    assert v["classification"] == "NEUTRAL"
    assert v["classified_by"] == "keywords"


# ── News cache + EWS #24 ─────────────────────────────────────────────────────

FAKE_NEWS = [
    {"title": "SYNTH Co wins export award", "link": "https://n/1",
     "source": "Wire", "published": "2026-06-01T00:00:00"},
    {"title": "SYNTH Co dragged to NCLT over default", "link": "https://n/2",
     "source": "Wire", "published": "2026-06-02T00:00:00"},
]


def test_refresh_news_and_ews_trigger(ingested, monkeypatch):
    data, bid, expected, db = ingested
    monkeypatch.setattr(news_intel, "fetch_news",
                        lambda name, days=365, max_items=25: (FAKE_NEWS, None))

    r = refresh_borrower_news(BORROWER, db_path=db, use_llm=False)
    assert r["error"] == ""
    assert r["new"] == 2 and r["adverse"] == 1
    assert count_adverse(bid, db_path=db) == 1

    # second run: same links, nothing new
    r2 = refresh_borrower_news(BORROWER, db_path=db, use_llm=False)
    assert r2["new"] == 0

    ews = run_full_ews(BORROWER, db_path=db)
    ind24 = next(i for i in ews.ews_indicators if i["id"] == 24)
    assert ind24["triggered"]
    assert not ews.rfa_required          # one Medium does not make RFA


def test_news_unknown_borrower(temp_db):
    r = refresh_borrower_news("Ghost Co", db_path=temp_db)
    assert "not found" in r["error"]


# ── Registry checks ──────────────────────────────────────────────────────────

def test_gaps_then_clear_then_stale_logic(ingested):
    data, bid, expected, db = ingested
    gaps = due_diligence_gaps(BORROWER, db_path=db)
    assert len(gaps) == len(REGISTRIES)          # nothing checked yet

    record_check(BORROWER, "gst", "clear",
                 remarks="GSTR-3B filed through Apr 2026", db_path=db)
    gaps = due_diligence_gaps(BORROWER, db_path=db)
    assert len(gaps) == len(REGISTRIES) - 1

    checks = {c["registry"]: c for c in get_checks(BORROWER, db_path=db)}
    assert checks["gst"]["status"] == "clear"
    assert checks["mca"]["status"] == "unchecked"
    assert checks["ibbi"]["url"].startswith("https://ibbi.gov.in")


def test_adverse_gst_triggers_critical_ews_and_rfa(ingested):
    """GST arrears map to EWS #1 (Critical) → RFA condition fires."""
    data, bid, expected, db = ingested
    record_check(BORROWER, "gst", "adverse",
                 remarks="GSTR-3B not filed for 4 months", db_path=db)
    ews = run_full_ews(BORROWER, db_path=db)
    hit = [i for i in ews.ews_indicators
           if i.get("source") == "registry:gst"]
    assert hit and hit[0]["severity"] == "Critical"
    assert ews.rfa_required
    assert "statutory" in ews.rfa_reason.lower() or "GST" in ews.rfa_reason


def test_record_check_validation(ingested):
    data, bid, expected, db = ingested
    with pytest.raises(ValueError):
        record_check(BORROWER, "interpol", "clear", db_path=db)
    with pytest.raises(ValueError):
        record_check(BORROWER, "gst", "maybe", db_path=db)
    with pytest.raises(ValueError):
        record_check("Ghost Co", "gst", "clear", db_path=db)


def test_due_diligence_gap_becomes_committee_query(ingested):
    from report.committee_queries import generate_queries
    data, bid, expected, db = ingested
    queries = generate_queries(BORROWER, db_path=db)
    dd = [q for q in queries if q["parameter"] == "Due diligence"]
    assert dd and "MCA charges" in dd[0]["question"]
