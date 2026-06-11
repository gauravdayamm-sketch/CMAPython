"""Unit tests for the ETS forecasting module."""
import numpy as np
import pandas as pd
import pytest

from analytics.forecast import _fit_ets, run_annual_forecast, run_weekly_forecast


def test_fit_ets_continues_linear_trend():
    # +10/period trend with small noise (a perfectly straight line has zero
    # residual variance and makes the MLE optimizer complain)
    rng = np.random.default_rng(7)
    values = [100 + 10 * i + rng.normal(0, 0.5) for i in range(8)]
    series = pd.Series(values,
                       index=pd.date_range("2017-03-31", periods=8, freq="YE-MAR"))
    fit, fc, err = _fit_ets(series, horizon=2)
    assert err is None
    assert fc["mean"][0] == pytest.approx(180, rel=0.05)
    assert fc["mean"][1] == pytest.approx(190, rel=0.07)
    assert fc["lo_95"][0] < fc["mean"][0] < fc["hi_95"][0]


def test_fit_ets_rejects_short_history():
    series = pd.Series([100, 110, 120])
    fit, fc, err = _fit_ets(series, horizon=2)
    assert fit is None and fc is None
    assert "4 periods" in err


def test_fit_ets_ignores_zero_placeholder_rows():
    # Zeros are unpopulated cells: only 3 real values remain -> error
    series = pd.Series([100, 0, 110, 0, 120])
    _, _, err = _fit_ets(series, horizon=1)
    assert err is not None


def test_annual_forecast_from_db(seeded_db):
    results = run_annual_forecast()
    assert len(results) == 5
    for r in results:
        assert r.error == "", f"{r.metric}: {r.error}"
        assert len(r.forecast) == 2
        assert r.smape < 0.10, f"{r.metric} fit unexpectedly poor"

    sales = next(r for r in results if "Sales" in r.metric)
    # Seeded sales grow ~7-8% a year and end at 2040
    assert sales.forecast[0].point > 2040
    assert sales.forecast[0].period == "2025-03-31"


def test_annual_forecast_empty_db(temp_db):
    assert run_annual_forecast() == []


def test_weekly_forecast_from_db(seeded_db):
    r = run_weekly_forecast(opening_cash=50.0, cc_limit=80.0)
    assert r.borrower_name == "Test Borrower Ltd"
    assert len(r.receipts_forecast) == 13
    assert len(r.cumulative) == 13
    assert r.verdict != ""
    assert r.min_cash is not None


def test_weekly_forecast_insufficient_history(temp_db):
    r = run_weekly_forecast()
    assert "No borrower" in r.verdict or "at least 8 weeks" in r.verdict
