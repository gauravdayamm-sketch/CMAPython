"""Tests for the EWS engine: red flags, exception engine, RBI EWS + RFA rule."""
import sqlite3
import pytest

from ingest.demo_fixture import BORROWER
from analytics.ews import (
    run_full_ews, red_flag_verdict, Flag, EWS_CATALOGUE,
)


def _tamper(db, bid, line_code, value, where="audited"):
    """Overwrite one line of the latest audited (or dual) statement."""
    cond = ("statement_type='audited' AND is_dual=0 ORDER BY col_index DESC"
            if where == "audited" else "is_dual=1")
    with sqlite3.connect(db) as conn:
        conn.execute(f"""
            UPDATE cma_line SET value = ?
            WHERE line_code = ? AND stmt_id =
                (SELECT stmt_id FROM cma_statement
                 WHERE borrower_id=? AND {cond} LIMIT 1)
        """, (value, line_code, bid))


def test_clean_borrower_all_clear(ingested):
    data, bid, expected, db = ingested
    r = run_full_ews(BORROWER, db_path=db)
    assert r.error == ""
    assert len(r.red_flags) == 32
    assert len(r.exceptions) == 13
    assert r.triggered_red_flags == []
    assert r.triggered_exceptions == []
    assert not any(i["triggered"] for i in r.ews_indicators)
    assert r.red_flag_verdict == "NO MATERIAL RED FLAGS"
    assert not r.rfa_required


def test_loss_year_triggers_rf1(ingested):
    data, bid, expected, db = ingested
    _tamper(db, bid, "os_pat", -50.0)
    r = run_full_ews(BORROWER, db_path=db)
    rf1 = next(f for f in r.red_flags if f.rule_id == "RF1")
    assert rf1.status == "BREACH"
    assert r.red_flag_verdict != "NO MATERIAL RED FLAGS"


def test_negative_tnw_triggers_rf9(ingested):
    data, bid, expected, db = ingested
    _tamper(db, bid, "bs_tnw", -10.0)
    r = run_full_ews(BORROWER, db_path=db)
    assert next(f for f in r.red_flags if f.rule_id == "RF9").status == "BREACH"


def test_receivable_spike_triggers_ex1(ingested):
    data, bid, expected, db = ingested
    latest = data.year("2024-25", "audited").lines
    _tamper(db, bid, "bs_receivables_domestic",
            latest["bs_receivables_domestic"] * 2.5)
    r = run_full_ews(BORROWER, db_path=db)
    ex1 = next(f for f in r.exceptions if f.rule_id == "EX1")
    assert ex1.status == "BREACH"


def test_dso_jump_triggers_ews35(ingested):
    data, bid, expected, db = ingested
    latest = data.year("2024-25", "audited").lines
    _tamper(db, bid, "bs_receivables_domestic",
            latest["bs_receivables_domestic"] * 1.5)
    r = run_full_ews(BORROWER, db_path=db)
    ews35 = next(i for i in r.ews_indicators if i["id"] == 35)
    assert ews35["triggered"]


def test_rfa_rule():
    # one Critical → RFA; one High → no; two High → RFA
    assert EWS_CATALOGUE[19][1] == "Critical"
    assert EWS_CATALOGUE[2][1] == "High"


def test_rfa_via_manual_triggers(ingested):
    data, bid, expected, db = ingested
    one_high = run_full_ews(BORROWER, manual_triggers=[2], db_path=db)
    assert not one_high.rfa_required

    two_high = run_full_ews(BORROWER, manual_triggers=[2, 4], db_path=db)
    assert two_high.rfa_required
    assert "High-severity" in two_high.rfa_reason

    critical = run_full_ews(BORROWER, manual_triggers=[19], db_path=db)
    assert critical.rfa_required
    assert "Forensic audit" in critical.rfa_reason


def test_verdict_ladder():
    def flags(n_triggered):
        out = []
        for i in range(32):
            status = "BREACH" if i < n_triggered else "OK"
            out.append(Flag(f"RF{i+1}", "s", "t", 0, "x", status))
        return out
    assert red_flag_verdict(flags(0)) == "NO MATERIAL RED FLAGS"
    assert "MINOR" in red_flag_verdict(flags(3))
    assert "MULTIPLE" in red_flag_verdict(flags(5))
    assert "MATERIAL" in red_flag_verdict(flags(9))


def test_project_finance_overlay_flags(ingested, tmp_path):
    """PF under-construction borrowers get the regulatory VERIFY flags."""
    import openpyxl
    from ingest.cma_workbook import ingest_workbook
    data, bid, expected, db = ingested
    with sqlite3.connect(db) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO cma_setup_meta (borrower_id, key, value)
            VALUES (?, 'project_finance', 'Yes'),
                   (?, 'project_phase', 'Under-Construction'),
                   (?, 'project_sector', 'Infrastructure')
        """, (bid, bid, bid))
    r = run_full_ews(BORROWER, db_path=db)
    assert next(f for f in r.red_flags if f.rule_id == "RF31").status == "VERIFY"
    assert next(f for f in r.red_flags if f.rule_id == "RF32").status == "VERIFY"
