"""Tests for peer benchmarking and industry norms."""
import sqlite3
import pytest

from analytics.benchmark import (
    benchmark, set_norms, get_norms, set_industry,
)
from ingest.demo_fixture import BORROWER


def _seed_peer(db, name, industry, sales, ebitda, ni, ca, cl, recv):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO borrower (name, industry) VALUES (?, ?)",
            (name, industry))
        bid = conn.execute(
            "SELECT borrower_id FROM borrower WHERE name=?",
            (name,)).fetchone()[0]
        for i, fy in enumerate(("2024-03-31", "2025-03-31")):
            f = 1.0 + 0.08 * i
            conn.execute("""
                INSERT INTO financials
                (borrower_id, fy_end, audited, sales, ebitda, net_income,
                 current_assets, current_liabilities, receivables,
                 total_assets, total_liabilities, tnw, tol)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (bid, fy, sales * f, ebitda * f, ni * f, ca * f, cl * f,
                  recv * f, sales * f * 0.8, sales * f * 0.4,
                  sales * f * 0.3, sales * f * 0.4))


@pytest.fixture
def peered(ingested):
    data, bid, expected, db = ingested
    set_industry(BORROWER, "Auto Components", db_path=db)
    for i, margin in enumerate((0.06, 0.09, 0.12)):
        _seed_peer(db, f"Peer {i} Pvt Ltd", "Auto Components",
                   sales=1000, ebitda=1000 * margin, ni=1000 * margin * 0.5,
                   ca=400, cl=300, recv=150)
    return db


def test_benchmark_percentiles(peered):
    r = benchmark(BORROWER, db_path=peered)
    assert r["error"] == ""
    assert "industry peers" in r["peer_set"]
    assert r["n_peers"] == 3
    rows = {row["key"]: row for row in r["rows"]}
    # Demo borrower's EBITDA margin ~10.2% sits between the 9% and 12% peers
    assert rows["ebitda_margin"]["percentile"] in (33, 67)
    assert rows["current_ratio"]["n_peers"] == 3


def test_benchmark_falls_back_to_whole_book(ingested):
    data, bid, expected, db = ingested
    _seed_peer(db, "Lone Peer", "Shipping", 500, 50, 20, 200, 150, 80)
    r = benchmark(BORROWER, db_path=db)
    assert "whole book" in r["peer_set"]


def test_norms_roundtrip_and_validation(temp_db):
    set_norms("Textiles", {"ebitda_margin": 0.07, "tol_tnw": 2.0},
              source="CRISIL", db_path=temp_db)
    norms = get_norms("Textiles", db_path=temp_db)
    assert {n["metric"] for n in norms} == {"ebitda_margin", "tol_tnw"}
    with pytest.raises(ValueError):
        set_norms("Textiles", {"made_up_metric": 1}, db_path=temp_db)


def test_norms_appear_in_benchmark(peered):
    set_norms("Auto Components", {"ebitda_margin": 0.08},
              source="ICRA", db_path=peered)
    r = benchmark(BORROWER, db_path=peered)
    row = next(x for x in r["rows"] if x["key"] == "ebitda_margin")
    assert row["norm_median"] == pytest.approx(0.08)
    assert row["norm_source"] == "ICRA"


