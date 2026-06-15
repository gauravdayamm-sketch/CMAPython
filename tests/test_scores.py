"""Tests for the Altman/Zmijewski/Ohlson distress ensemble and memo audit."""

from analytics.scores import altman_z2, zmijewski_x, run_ensemble
from agents.committee import verify_memo_numbers
from ingest.demo_fixture import BORROWER


# ── Altman Z″ ─────────────────────────────────────────────────────────────────

def test_altman_healthy_is_safe():
    r = altman_z2(ta=1000, wc=300, retained_earnings=350, ebit=150,
                  tnw=550, tl=450)
    assert r.zone == "SAFE"
    assert r.score > 2.6


def test_altman_distressed():
    r = altman_z2(ta=800, wc=-100, retained_earnings=-50, ebit=-40,
                  tnw=50, tl=750)
    assert r.zone == "DISTRESS"
    assert r.score < 1.1


def test_altman_rejects_bad_inputs():
    assert altman_z2(ta=0, wc=1, retained_earnings=1, ebit=1, tnw=1, tl=1) is None


# ── Zmijewski ─────────────────────────────────────────────────────────────────

def test_zmijewski_healthy_low_probability():
    r = zmijewski_x(ta=1000, tl=400, ni=90, ca=500, cl=300)
    assert r.zone == "SAFE"
    assert r.prob < 0.30


def test_zmijewski_overleveraged_flags():
    r = zmijewski_x(ta=1000, tl=950, ni=-60, ca=200, cl=400)
    assert r.zone == "DISTRESS"
    assert r.prob > 0.50


# ── Ensemble on the demo borrower (CMA-sourced inputs) ────────────────────────

def test_ensemble_on_clean_borrower(ingested):
    data, bid, expected, db = ingested
    r = run_ensemble(BORROWER, db_path=db)
    assert r.error == ""
    assert r.fy_end == "2025-03-31"          # latest audited, not projections
    assert r.ohlson and r.altman and r.zmijewski
    assert r.altman.note == ""               # true RE from CMA reserves
    # Altman and Zmijewski read the healthy borrower correctly; Ohlson's
    # 1980 US calibration runs hot on creditor-financed Indian SMEs
    # (TOL/TA ≈ 0.55+) — exactly the disagreement the ensemble surfaces.
    assert r.altman.zone == "SAFE"
    assert r.zmijewski.zone == "SAFE"
    assert r.models_flagging <= 1
    assert not r.verdict.startswith("DISTRESS CONSENSUS")


def test_ensemble_financials_fallback(seeded_db):
    """Borrowers without CMA data use financials with the RE approximation."""
    r = run_ensemble("Test Borrower Ltd")
    assert r.error == ""
    assert "approximated" in r.altman.note


def test_ensemble_unknown_borrower(temp_db):
    assert run_ensemble("Ghost Co").error != ""


# ── Memo number audit ─────────────────────────────────────────────────────────

SOURCE = ["{'ohlson_pd': 0.408, 'sales': 1815.0, 'dscr': 1.62}"]


def test_verifier_accepts_traceable_numbers():
    memo = ("PD stands at 40.8% on sales of 1,815 Cr. "
            "DSCR of 1.62x meets covenant.")
    audit = verify_memo_numbers(memo, SOURCE)
    assert audit["unverified"] == []
    assert audit["total"] == 3


def test_verifier_flags_invented_numbers():
    memo = "PD stands at 40.8% but leverage of 7.93x is alarming."
    audit = verify_memo_numbers(memo, SOURCE)
    assert "7.93" in audit["unverified"]


def test_verifier_ignores_years_and_list_indices():
    memo = "1. In FY 2024 the company performed. 2. See section 3."
    audit = verify_memo_numbers(memo, SOURCE)
    assert audit["total"] == 0
