"""Tests for AOC-4 tie-out and audit-report narrative (no PDF/OCR needed)."""
import pytest

from data.mca_filings import (
    parse_aoc4_text, tie_out_aoc4, analyse_report_text, _record,
)
from analytics.ews import run_full_ews
from ingest.demo_fixture import BORROWER


# A minimal flattened AOC-4 text in the real e-Form's idiom.
def _aoc4(total_income, pbt, pat, net_worth, recv):
    return (
        "Form No. AOC-4 Form for filing financial statement "
        "(a) Corporate identity number (CIN) U12345MH2020PTC100000 "
        "Financial year to which financial statements relates "
        "From (DD/MM/YYYY) 01/04/2024 To (DD/MM/YYYY) 31/03/2025 "
        "Nature of financial statements Adopted Financial statements "
        f"Total Income (I+II) {int(total_income*1e7)}.00 34781453.00 "
        f"(IX) Profit before tax (VII-VIII) {int(pbt*1e7)}.00 454731.00 "
        f"for the period from continuing operations (IX-X) {int(pat*1e7)}.00 519017.00 "
        f"Net Worth of the company {int(net_worth*1e7)} "
        f"Trade receivables {int(recv*1e7)} 198390 "
        "Authorised capital of the company as on the date of filing 15000000")


def test_parse_aoc4_fields():
    f = parse_aoc4_text(_aoc4(6.30, 0.28, 0.27, 1.10, 0.41))
    assert f["cin"] == "U12345MH2020PTC100000"
    assert f["fy_to"] == "2025-31-03"[:4] + "-03-31"  # 2025-03-31
    assert f["figures"]["total_income"] == pytest.approx(6.30, abs=0.01)
    assert f["figures"]["pbt"] == pytest.approx(0.28, abs=0.01)
    assert f["figures"]["pat"] == pytest.approx(0.27, abs=0.01)
    assert f["figures"]["net_worth"] == pytest.approx(1.10, abs=0.01)


def test_tie_out_clean(ingested):
    """Filed figures equal to the CMA audited year → all OK, no breach."""
    data, bid, expected, db = ingested
    L = data.year("2024-25", "audited").lines
    filing = parse_aoc4_text(_aoc4(
        L["os_net_sales"], L["os_pbt"], L["os_pat"], L["bs_tnw"],
        L["bs_receivables_domestic"] + L["bs_receivables_export"]))
    r = tie_out_aoc4(BORROWER, filing, db_path=db)
    assert r["error"] == ""
    assert r["breaches"] == []
    assert all(row["status"] == "ok" for row in r["rows"])


def test_tie_out_detects_concealment(ingested):
    """Filed PAT materially below the CMA figure → MISMATCH + breach."""
    data, bid, expected, db = ingested
    L = data.year("2024-25", "audited").lines
    filing = parse_aoc4_text(_aoc4(
        L["os_net_sales"], L["os_pbt"], L["os_pat"] * 0.4,   # PAT understated
        L["bs_tnw"], L["bs_receivables_domestic"] + L["bs_receivables_export"]))
    r = tie_out_aoc4(BORROWER, filing, db_path=db)
    pat_row = next(row for row in r["rows"] if "after tax" in row["item"])
    assert pat_row["status"] == "MISMATCH"
    assert any("Profit after tax" in b for b in r["breaches"])


def test_tie_out_breach_feeds_ews_18(ingested):
    data, bid, expected, db = ingested
    _record(BORROWER, "tie_out", 18, "Critical",
            "AOC-4 vs CMA mismatch — Profit after tax: CMA 1.20 vs filed 0.40",
            "AOC-4.pdf", db)
    ews = run_full_ews(BORROWER, db_path=db)
    hit = [i for i in ews.ews_indicators if i.get("source") == "filing:tie_out"]
    assert hit and hit[0]["severity"] == "Critical"
    assert ews.rfa_required


# ── Audit-report narrative (negation-aware, OCR-tolerant) ────────────────────

CLEAN_REPORT = (
    "Independent Auditor's Report. In our opinion the financial statements "
    "give a true and fair view. The company is regular in depositing "
    "undisputed statutory dues and there were no outstanding statutory dues "
    "as on March 31, 2025. As part of our responsibilities we conclude on "
    "the appropriateness of the going concern basis and whether a material "
    "uncertainty exists.")

QUALIFIED_REPORT = (
    "Basis for Qualified Opinion. In our qualified opinion, except for the "
    "effects of the matter described, the statements give a true and fair "
    "view. The company has not been regular in depositing statutory dues "
    "and undisputed statutory dues were in arrears as at year end.")


def test_clean_report_no_false_positives():
    r = analyse_report_text(CLEAN_REPORT)
    assert r["opinion"] == "UNMODIFIED"
    assert r["statutory_arrears"] is False        # negation respected
    assert r["auto_findings"] == []               # nothing fed to EWS


def test_qualified_report_detected():
    r = analyse_report_text(QUALIFIED_REPORT)
    assert r["opinion"] == "QUALIFIED"
    assert r["statutory_arrears"] is True
    assert r["auto_findings"] and r["auto_findings"][0][0] == 38


def test_qualified_opinion_feeds_ews_38(ingested):
    data, bid, expected, db = ingested
    _record(BORROWER, "auditor_opinion", 38, "High",
            "Auditor opinion QUALIFIED (per scanned report)", "report.pdf", db)
    ews = run_full_ews(BORROWER, db_path=db)
    hit = [i for i in ews.ews_indicators
           if i.get("source") == "filing:auditor_opinion"]
    assert hit and hit[0]["severity"] == "High"
