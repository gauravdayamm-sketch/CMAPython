"""Unit tests for the Ohlson O-Score module."""
import math
import pytest

from analytics.ohlson import compute_ohlson, compute_ohlson_from_dict, COEFFICIENTS


def test_healthy_borrower_low_risk():
    r = compute_ohlson(ta=1500, tl=450, ca=700, cl=350, wc=350,
                       ni=180, ffo=220, ni_prev=150)
    assert r is not None
    assert r.prob_default < 0.20
    assert r.risk_band in ("LOW - Healthy", "WATCH")


def test_distressed_borrower_high_risk():
    r = compute_ohlson(ta=800, tl=700, ca=300, cl=400, wc=-100,
                       ni=-30, ffo=-10, ni_prev=-20)
    assert r is not None
    assert r.prob_default > 0.50
    assert r.risk_band == "SEVERE"
    # Two consecutive loss years must set INTWO
    assert r.inputs["INTWO"] == 1


def test_oeneg_flag_when_liabilities_exceed_assets():
    r = compute_ohlson(ta=500, tl=600, ca=200, cl=250, wc=-50,
                       ni=-10, ffo=5, ni_prev=10)
    assert r.inputs["OENEG"] == 1


def test_contributions_sum_to_o_score():
    r = compute_ohlson(ta=1200, tl=500, ca=600, cl=300, wc=300,
                       ni=100, ffo=130, ni_prev=80)
    assert math.isclose(sum(r.contributions.values()), r.o_score, abs_tol=1e-3)
    assert len(r.contributions) == len(COEFFICIENTS)


def test_probability_is_logistic_of_score():
    r = compute_ohlson(ta=1000, tl=400, ca=500, cl=250, wc=250,
                       ni=90, ffo=110, ni_prev=85)
    expected = 1.0 / (1.0 + math.exp(-r.o_score))
    assert math.isclose(r.prob_default, expected, abs_tol=1e-3)


def test_zero_assets_returns_error_dict():
    out = compute_ohlson_from_dict({"total_assets": 0})
    assert "error" in out


def test_dict_wrapper_round_trip():
    out = compute_ohlson_from_dict({
        "total_assets": 1500, "total_liabilities": 450,
        "current_assets": 700, "current_liabilities": 350,
        "working_capital": 350, "net_income": 180, "ffo": 220,
        "net_income_prev": 150,
    })
    assert set(out) == {"o_score", "prob_default", "risk_band", "contributions"}
    assert 0.0 <= out["prob_default"] <= 1.0
