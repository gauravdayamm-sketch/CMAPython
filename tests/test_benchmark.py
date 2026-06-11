"""Tests for peer benchmarking, industry norms and the note check."""
import sqlite3
import pytest

from analytics.benchmark import (
    benchmark, set_norms, get_norms, set_industry, METRICS,
)
from report.note_check import check_note
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


# ── Note check ────────────────────────────────────────────────────────────────

def _make_note(path, lines):
    from docx import Document
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    doc.save(path)


def test_note_check_traces_real_figures(ingested, tmp_path):
    data, bid, expected, db = ingested
    latest = data.year("2024-25", "audited").lines
    note = tmp_path / "note.docx"
    _make_note(note, [
        f"Net sales for FY25 stood at {latest['os_net_sales']:.2f} Cr.",
        f"TNW is {latest['bs_tnw']:.2f} Cr and current ratio is healthy.",
        "Proposed limit: 220.00 Cr against existing 180.00 Cr.",
    ])
    r = check_note(note, borrower_name=BORROWER, db_path=db)
    assert r["error"] == ""
    assert r["untraced"] == []
    assert r["traced"] == r["total"] > 0


def test_note_check_flags_invented_figure(ingested, tmp_path):
    data, bid, expected, db = ingested
    note = tmp_path / "note2.docx"
    _make_note(note, [
        "The company earned a PAT of 999.77 Cr last year.",   # invented
        "CIN U28999GJ2020PTC118078 and branch code (60344).",  # id noise
    ])
    r = check_note(note, borrower_name=BORROWER, db_path=db)
    figures = [u["figure"] for u in r["untraced"]]
    assert "999.77" in figures
    assert "28999" not in figures        # identifier digits skipped
    assert "60344" not in figures        # code-like integer skipped
    assert "PAT of 999.77" in r["untraced"][0]["context"]


def test_note_check_no_cma_data(temp_db, tmp_path):
    note = tmp_path / "note3.docx"
    _make_note(note, ["Sales of 100.55 Cr."])
    r = check_note(note, borrower_name="Ghost Co", db_path=temp_db)
    assert r["error"] != ""
