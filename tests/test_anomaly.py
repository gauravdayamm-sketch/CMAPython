"""Unit tests for the three anomaly engines, incl. regressions for fixed bugs."""
import sqlite3
import pytest

from analytics.anomaly import (
    run_beneish, run_rolling_z, run_isoforest,
    run_all_anomaly, save_anomaly_to_db,
)


def _year(sales, *, recv=200, cogs=None, cl=400, ltd=100, ta=1200, ca=600,
          ppe=400, sec=10, sga=150, dep=25, ni=100, ffo=125, tl=500, ebitda=180):
    return {
        "sales": sales, "receivables": recv, "cogs": cogs or sales * 0.7,
        "current_liabilities": cl, "long_term_debt": ltd, "total_assets": ta,
        "current_assets": ca, "ppe": ppe, "securities": sec, "sga": sga,
        "depreciation": dep, "net_income": ni, "ffo": ffo,
        "total_liabilities": tl, "ebitda": ebitda,
    }


# ── Beneish ──────────────────────────────────────────────────────────────────

def test_beneish_lvgi_uses_current_liabilities():
    """Regression: LVGI must be built from CL + LTD, not current assets."""
    prev = _year(1500, cl=300, ltd=100, ta=1000, ca=999)
    curr = _year(1600, cl=600, ltd=100, ta=1000, ca=1)  # CL doubled, CA collapsed
    r = run_beneish(curr, prev)
    expected_lvgi = ((600 + 100) / 1000) / ((300 + 100) / 1000)  # 1.75
    assert r.components["LVGI"] == pytest.approx(expected_lvgi, abs=1e-3)


def test_beneish_dsri_neutral_when_receivables_missing():
    """Regression: missing receivables must give DSRI=1.0, not 0.0."""
    prev = _year(1500, recv=None)
    curr = _year(1600, recv=None)
    r = run_beneish(curr, prev)
    assert r.components["DSRI"] == 1.0


def test_beneish_flags_aggressive_receivables_growth():
    prev = _year(1500, recv=150)
    curr = _year(1650, recv=600)   # receivables 4x on 10% sales growth
    r = run_beneish(curr, prev)
    assert r.components["DSRI"] > 3.0
    assert r.m_score > -2.22       # at least grey zone


def test_beneish_stable_company_not_flagged():
    prev = _year(1500)
    curr = _year(1575, recv=210)   # 5% growth, proportional receivables
    r = run_beneish(curr, prev)
    assert not r.is_manipulator
    assert "NON-MANIPULATOR" in r.verdict


# ── Rolling Z-score ──────────────────────────────────────────────────────────

def _rows(opms, sales_base=1000):
    """Build minimal financials rows with a controlled OPM path."""
    rows = []
    for i, opm in enumerate(opms):
        sales = sales_base * (1.0 + 0.10 * i)
        rows.append({
            "sales": sales, "ebitda": sales * opm, "net_income": sales * 0.05,
            "ffo": sales * 0.07 * (1 + 0.02 * i), "cogs": sales * 0.7,
            "receivables": sales * 0.12, "depreciation": 20,
        })
    return rows


def test_z_score_flags_genuine_margin_spike():
    rows = _rows([0.10, 0.11, 0.10, 0.25])   # latest margin jumps 2.5x
    results = run_rolling_z(rows)
    opm = next(r for r in results if "Margin" in r.metric and "Operating" in r.metric)
    assert opm.verdict in ("WARNING", "CRITICAL")
    assert opm.z_score > 2.0


def test_z_score_flat_history_not_critical():
    """Regression: float-noise deviations must not produce |Z| ~ 10."""
    rows = _rows([0.10, 0.10, 0.10, 0.1001])
    results = run_rolling_z(rows)
    for r in results:
        if "Operating" in r.metric:
            assert abs(r.z_score) < 1.5, f"flat history flagged: Z={r.z_score}"


def test_z_score_skips_metric_when_latest_year_missing():
    """Regression: a None in the latest year must skip the metric, not
    silently use an older year's value as 'latest'."""
    rows = _rows([0.10, 0.11, 0.10, 0.12])
    rows[-1]["receivables"] = None            # DSO unavailable in latest year
    results = run_rolling_z(rows)
    assert not any("DSO" in r.metric for r in results)


def test_z_score_needs_four_years():
    assert run_rolling_z(_rows([0.10, 0.11, 0.12])) == []


# ── IsolationForest ──────────────────────────────────────────────────────────

def _iso_row(bid, fy, *, tl=400, ta=1000, ca=500, cl=300, ni=80, ffo=100,
             ebitda=150, sales=1400):
    return {
        "borrower_id": bid, "fy_end": fy, "total_liabilities": tl,
        "total_assets": ta, "current_assets": ca, "current_liabilities": cl,
        "net_income": ni, "ffo": ffo, "ebitda": ebitda, "sales": sales,
    }


def test_isoforest_flags_extreme_outlier():
    rows = [_iso_row(i, "2024-03-31") for i in range(1, 12)]
    rows.append(_iso_row(99, "2024-03-31", tl=5000, ni=-900, ffo=-800, ebitda=-500))
    r = run_isoforest(rows, target_borrower_id=99, target_fy="2024-03-31")
    assert r.is_anomaly
    assert r.verdict == "ANOMALY FLAGGED"


def test_isoforest_insufficient_data():
    rows = [_iso_row(i, "2024-03-31") for i in range(1, 5)]
    r = run_isoforest(rows, target_borrower_id=1, target_fy="2024-03-31")
    assert not r.is_anomaly
    assert "INSUFFICIENT" in r.verdict


# ── End-to-end against the seeded temp DB ────────────────────────────────────

def test_run_all_anomaly_end_to_end(seeded_db):
    report = run_all_anomaly()
    assert report.borrower_name == "Test Borrower Ltd"
    assert report.fy_end == "2024-03-31"
    assert report.beneish is not None
    assert isinstance(report.beneish.m_score, float)
    assert report.z_scores, "5 populated years must yield z-scores"
    assert report.master_verdict.split(" ")[0] in ("CLEAN", "MODERATE", "HIGH")


def test_save_anomaly_replaces_instead_of_duplicating(seeded_db):
    """Regression: repeated runs must not grow anomaly_result unboundedly."""
    report = run_all_anomaly()
    save_anomaly_to_db(report)
    save_anomaly_to_db(report)
    save_anomaly_to_db(report)
    with sqlite3.connect(seeded_db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM anomaly_result GROUP BY borrower_id, detector"
        ).fetchall()
    assert all(count == (1,) for count in n), f"duplicate detector rows: {n}"
