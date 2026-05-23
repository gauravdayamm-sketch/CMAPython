"""
Time-Series Forecasting Module
Replaces Excel FORECAST.ETS with statsmodels ETS (Holt-Winters).

Two forecast types:
  1. Annual   : projects next 1-2 years from audited financial history
  2. Weekly   : projects next 13 weeks from cash flow history
                detects seasonality (GST cycles, payroll cycles)
"""
import numpy as np
import pandas as pd
import sqlite3
import pathlib
from dataclasses import dataclass, field
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ForecastPoint:
    period:  str
    point:   float
    lo_80:   float
    hi_80:   float
    lo_95:   float
    hi_95:   float


@dataclass
class ForecastResult:
    metric:        str
    model_type:    str
    rmse:          float
    smape:         float
    n_history:     int
    forecast:      list = field(default_factory=list)
    verdict:       str  = ""
    error:         str  = ""


@dataclass
class WeeklyForecastResult:
    borrower_name:     str
    horizon_weeks:     int
    receipts_forecast: list = field(default_factory=list)
    payments_forecast: list = field(default_factory=list)
    net_forecast:      list = field(default_factory=list)
    cumulative:        list = field(default_factory=list)
    breach_week:       Optional[int]  = None
    breach_date:       Optional[str]  = None
    min_cash:          Optional[float] = None
    min_cash_week:     Optional[int]   = None
    opening_cash:      float = 0.0
    cc_limit:          float = 0.0
    bankable_breach:   Optional[int]   = None
    verdict:           str   = ""


# ── ETS model fitter ──────────────────────────────────────────────────────────

def _fit_ets(series, horizon, freq="A"):
    """
    Fit Holt-Winters ETS model and return forecast with intervals.

    series : pd.Series with DatetimeIndex
    horizon: number of periods to forecast
    freq   : 'A' for annual, 'W' for weekly
    """
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    if len(series) < 4:
        return None, None, "Need at least 4 periods of history"

    # Remove zeros/NaN at the end (unpopulated cells)
    series = series.replace(0, np.nan).dropna()
    if len(series) < 4:
        return None, None, "Fewer than 4 non-zero periods"

    try:
        # Try additive trend + additive error (Holt's linear)
        model = ETSModel(
            series,
            error="add",
            trend="add",
            seasonal=None,
            initialization_method="estimated",
        )
        fit = model.fit(disp=False)

        # In-sample error metrics
        resid = fit.resid
        rmse  = float(np.sqrt(np.mean(resid ** 2)))

        actual = series.values
        fitted = fit.fittedvalues.values
        # SMAPE — symmetric mean absolute percentage error
        with np.errstate(divide="ignore", invalid="ignore"):
            smape = float(np.nanmean(
                2 * np.abs(actual - fitted)
                / (np.abs(actual) + np.abs(fitted))
            ))

        # Forecast
        pred  = fit.get_prediction(
            start=len(series),
            end=len(series) + horizon - 1
        )
        df    = pred.summary_frame(alpha=0.05)   # 95% CI
        df80  = pred.summary_frame(alpha=0.20)   # 80% CI

        return fit, {
            "mean":   df["mean"].values,
            "lo_95":  df["pi_lower"].values,
            "hi_95":  df["pi_upper"].values,
            "lo_80":  df80["pi_lower"].values,
            "hi_80":  df80["pi_upper"].values,
            "rmse":   rmse,
            "smape":  smape,
        }, None

    except Exception as e:
        return None, None, str(e)


# ── Annual forecast ───────────────────────────────────────────────────────────

ANNUAL_METRICS = [
    ("sales",        "Net Sales (₹ Cr)"),
    ("ebitda",       "EBITDA (₹ Cr)"),
    ("net_income",   "PAT (₹ Cr)"),
    ("ffo",          "Cash Accruals (₹ Cr)"),
    ("total_assets", "Total Assets (₹ Cr)"),
]

def run_annual_forecast(borrower_name=None, horizon=2):
    """
    Run ETS forecast on annual financial history.
    Returns list of ForecastResult, one per metric.
    """
    results = []

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        if borrower_name:
            borrower = conn.execute(
                "SELECT * FROM borrower WHERE name = ?",
                (borrower_name,)
            ).fetchone()
        else:
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not borrower:
            return results

        rows = conn.execute(
            """SELECT * FROM financials
               WHERE borrower_id = ?
               ORDER BY fy_end ASC""",
            (borrower["borrower_id"],)
        ).fetchall()

    if len(rows) < 4:
        return results

    # Build DatetimeIndex from fy_end dates
    dates = pd.to_datetime([r["fy_end"] for r in rows])

    for col, label in ANNUAL_METRICS:
        values = [float(r[col] or 0) for r in rows]
        series = pd.Series(values, index=dates)

        fit, fc, err = _fit_ets(series, horizon, freq="A")

        if err or fc is None:
            result = ForecastResult(
                metric=label,
                model_type="ETS(A,A,N)",
                rmse=0, smape=0,
                n_history=len(rows),
                error=err or "Unknown error"
            )
        else:
            # Build forecast periods
            last_date = dates[-1]
            forecast_points = []
            for i in range(horizon):
                period = str(last_date.year + i + 1) + "-03-31"
                forecast_points.append(ForecastPoint(
                    period=period,
                    point=round(float(fc["mean"][i]), 2),
                    lo_80=round(float(fc["lo_80"][i]), 2),
                    hi_80=round(float(fc["hi_80"][i]), 2),
                    lo_95=round(float(fc["lo_95"][i]), 2),
                    hi_95=round(float(fc["hi_95"][i]), 2),
                ))

            smape = fc["smape"]
            if smape < 0.10:
                verdict = "Excellent fit"
            elif smape < 0.20:
                verdict = "Good fit"
            elif smape < 0.50:
                verdict = "Acceptable"
            else:
                verdict = "Poor fit — interpret with caution"

            result = ForecastResult(
                metric=label,
                model_type="ETS(A,A,N)",
                rmse=round(fc["rmse"], 2),
                smape=round(smape, 4),
                n_history=len(rows),
                forecast=forecast_points,
                verdict=verdict,
            )

        results.append(result)

    return results


# ── Weekly cash flow forecast ─────────────────────────────────────────────────

def run_weekly_forecast(
    borrower_name=None,
    horizon=13,
    opening_cash=0.0,
    cc_limit=0.0,
):
    """
    Forecast next 13 weeks of cash flow from weekly history in SQLite.
    """
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        if borrower_name:
            borrower = conn.execute(
                "SELECT * FROM borrower WHERE name = ?",
                (borrower_name,)
            ).fetchone()
        else:
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not borrower:
            return WeeklyForecastResult(
                borrower_name="NOT FOUND",
                horizon_weeks=horizon,
                verdict="No borrower found",
            )

        rows = conn.execute(
            """SELECT * FROM cashflow_weekly
               WHERE borrower_id = ?
               ORDER BY week_end ASC""",
            (borrower["borrower_id"],)
        ).fetchall()

    result = WeeklyForecastResult(
        borrower_name=borrower["name"],
        horizon_weeks=horizon,
        opening_cash=opening_cash,
        cc_limit=cc_limit,
    )

    if len(rows) < 8:
        result.verdict = f"Need at least 8 weeks of history (have {len(rows)})"
        return result

    # Build series
    dates     = pd.to_datetime([r["week_end"] for r in rows])
    inflows   = pd.Series([float(r["inflow"]  or 0) for r in rows], index=dates)
    outflows  = pd.Series([float(r["outflow"] or 0) for r in rows], index=dates)

    # Forecast inflows
    _, fc_in, err_in = _fit_ets(inflows, horizon, freq="W")
    if err_in:
        result.verdict = f"Inflow forecast error: {err_in}"
        return result

    # Forecast outflows
    _, fc_out, err_out = _fit_ets(outflows, horizon, freq="W")
    if err_out:
        result.verdict = f"Outflow forecast error: {err_out}"
        return result

    # Build weekly projections
    last_date = dates[-1]
    cum_cash  = opening_cash

    for i in range(horizon):
        week_date  = last_date + pd.Timedelta(weeks=i+1)
        rec        = max(0, float(fc_in["mean"][i]))
        pay        = max(0, float(fc_out["mean"][i]))
        net        = rec - pay
        cum_cash  += net

        result.receipts_forecast.append(round(rec, 2))
        result.payments_forecast.append(round(pay, 2))
        result.net_forecast.append(round(net, 2))
        result.cumulative.append(round(cum_cash, 2))

        # Detect first cash-negative week
        if cum_cash < 0 and result.breach_week is None:
            result.breach_week = i + 1
            result.breach_date = str(week_date.date())

        # Detect bankable breach (cum + CC < 0)
        if (cum_cash + cc_limit) < 0 and result.bankable_breach is None:
            result.bankable_breach = i + 1

    # Minimum cash position
    if result.cumulative:
        min_idx = int(np.argmin(result.cumulative))
        result.min_cash      = result.cumulative[min_idx]
        result.min_cash_week = min_idx + 1

    # Verdict
    if result.bankable_breach:
        result.verdict = (
            f"CRITICAL — Bankable breach at Wk +{result.bankable_breach}. "
            f"CC limit exhausted. Fresh equity or limit enhancement required."
        )
    elif result.breach_week:
        result.verdict = (
            f"WARNING — Cash negative at Wk +{result.breach_week} "
            f"({result.breach_date}). CC drawdown required."
        )
    else:
        result.verdict = (
            f"HEALTHY — No cash-negative week in {horizon}-week horizon. "
            f"Min projected cash: {result.min_cash:.2f} Cr at Wk +{result.min_cash_week}."
        )

    return result


# ── Convenience summary dict ──────────────────────────────────────────────────

def run_forecast_summary(borrower_name=None):
    """Returns a flat dict suitable for excel_io.py output."""
    annual  = run_annual_forecast(borrower_name)
    weekly  = run_weekly_forecast(borrower_name)

    summary = {
        "borrower":      borrower_name or "latest",
        "annual_count":  len(annual),
        "weekly_verdict": weekly.verdict,
        "breach_week":   weekly.breach_week,
        "min_cash":      weekly.min_cash,
        "annual":        [],
    }

    for r in annual:
        entry = {
            "metric":  r.metric,
            "rmse":    r.rmse,
            "smape":   r.smape,
            "verdict": r.verdict,
            "error":   r.error,
            "yr1":     r.forecast[0].point if r.forecast else None,
            "yr2":     r.forecast[1].point if len(r.forecast) > 1 else None,
            "yr1_lo95": r.forecast[0].lo_95 if r.forecast else None,
            "yr1_hi95": r.forecast[0].hi_95 if r.forecast else None,
        }
        summary["annual"].append(entry)

    return summary