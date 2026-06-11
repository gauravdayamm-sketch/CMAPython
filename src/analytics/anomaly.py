"""
Anomaly Detection Module — three engines:
  1. Rolling Z-Score    : detects trend anomalies in operational metrics
  2. Beneish M-Score    : detects earnings manipulation (8 variables)
  3. IsolationForest    : multivariate anomaly detection (scikit-learn)
"""
import sqlite3
import pathlib
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ZScoreResult:
    metric:     str
    latest_val: float
    mean:       float
    std:        float
    z_score:    float
    verdict:    str
    action:     str

@dataclass
class BeneishResult:
    m_score:        float
    verdict:        str
    is_manipulator: bool
    components:     dict

@dataclass
class IsoForestResult:
    score:      float
    is_anomaly: bool
    verdict:    str

@dataclass
class AnomalyReport:
    borrower_name:  str
    fy_end:         str
    z_scores:       list = field(default_factory=list)
    beneish:        Optional[BeneishResult] = None
    isoforest:      Optional[IsoForestResult] = None
    master_verdict: str = "INSUFFICIENT DATA"


# ── Engine 1: Rolling Z-Score ─────────────────────────────────────────────────

Z_METRICS = [
    ("sales_growth",    "Sales Growth Y-o-Y",     "Validate revenue spike via GST/banking"),
    ("opm",             "Operating Profit Margin", "Examine margin deviation drivers"),
    ("inventory_days",  "Inventory Days",          "Stock audit — aging analysis required"),
    ("dso",             "DSO (Days Sales Outst.)", "Debtor aging — collection efficiency"),
    ("cash_profit_pct", "Cash Profit Margin",      "Cash gen vs accrual gap — manip signal"),
]

def _compute_ratios(rows):
    """Convert raw financials rows to ratio series."""
    series = {m: [] for m, _, _ in Z_METRICS}
    prev_sales = None

    for r in rows:
        sales = r["sales"] or 0
        ebitda = r["ebitda"] or 0
        ni = r["net_income"] or 0
        ffo = r["ffo"] or 0
        cogs = r["cogs"] or 0
        receivables = r["receivables"] or 0
        depreciation = r["depreciation"] or 0

        # Sales growth
        if prev_sales and prev_sales > 0:
            series["sales_growth"].append((sales - prev_sales) / prev_sales)
        else:
            series["sales_growth"].append(None)

        # OPM
        series["opm"].append(ebitda / sales if sales > 0 else None)

        # Inventory days — approximate from BS if available
        series["inventory_days"].append(None)  # populated from BS separately

        # DSO
        series["dso"].append(
            (receivables / sales * 365) if sales > 0 and receivables > 0 else None
        )

        # Cash profit margin
        series["cash_profit_pct"].append(ffo / sales if sales > 0 else None)

        prev_sales = sales

    return series

def run_rolling_z(rows, window=3):
    """
    Calculate rolling Z-score for each metric.
    Requires at least 4 years of data for meaningful results.
    """
    results = []
    if len(rows) < 4:
        return results

    ratios = _compute_ratios(rows)

    for metric_key, label, action in Z_METRICS:
        values = ratios[metric_key]

        # The metric must exist for the latest year — otherwise an older
        # year's value would silently be treated as "latest".
        if not values or values[-1] is None:
            continue

        latest = values[-1]
        prior  = [v for v in values[:-1] if v is not None]
        if len(prior) < 2:
            continue

        mu    = np.mean(prior)
        sigma = np.std(prior, ddof=1)

        # Floor sigma at 1% of the mean level: an unnaturally flat history
        # (or float noise) would otherwise produce absurd Z values.
        sigma = max(sigma, 0.01 * abs(mu))
        if sigma == 0:
            continue

        z = (latest - mu) / sigma

        if abs(z) > 3.0:
            verdict = "CRITICAL"
        elif abs(z) > 2.0:
            verdict = "WARNING"
        elif abs(z) > 1.5:
            verdict = "ELEVATED"
        else:
            verdict = "NORMAL"

        results.append(ZScoreResult(
            metric=label, latest_val=round(latest, 4),
            mean=round(mu, 4), std=round(sigma, 4),
            z_score=round(z, 4), verdict=verdict, action=action
        ))

    return results


# ── Engine 2: Beneish M-Score ─────────────────────────────────────────────────

# Beneish (1999) coefficients
BENEISH_COEFS = {
    "intercept": -4.84,
    "DSRI":  0.920,
    "GMI":   0.528,
    "AQI":   0.404,
    "SGI":   0.892,
    "DEPI":  0.115,
    "SGAI": -0.172,
    "TATA":  4.679,
    "LVGI": -0.327,
}
BENEISH_THRESHOLD = -1.78   # above this = likely manipulator

def _safe_div(a, b, default=0.0):
    try:
        return a / b if b and b != 0 else default
    except Exception:
        return default

def run_beneish(curr, prev):
    """
    Compute Beneish M-Score.
    curr, prev: dict-like rows from financials table.
    Returns BeneishResult.
    """
    try:
        # Extract values
        c_sales = curr["sales"] or 0
        p_sales = prev["sales"] or 0
        c_cogs  = curr["cogs"]  or 0
        p_cogs  = prev["cogs"]  or 0
        c_recv  = curr["receivables"] or 0
        p_recv  = prev["receivables"] or 0
        c_ta    = curr["total_assets"] or 0
        p_ta    = prev["total_assets"] or 0
        c_ppe   = curr["ppe"] or 0
        p_ppe   = prev["ppe"] or 0
        c_dep   = curr["depreciation"] or 0
        p_dep   = prev["depreciation"] or 0
        c_sga   = curr["sga"] or 0
        p_sga   = prev["sga"] or 0
        c_ni    = curr["net_income"] or 0
        c_ffo   = curr["ffo"] or 0
        c_tl    = curr["total_liabilities"] or 0
        c_ca    = curr["current_assets"] or 0
        c_cl    = curr["current_liabilities"] or 0
        c_sec   = curr["securities"] or 0
        c_ltd   = curr["long_term_debt"] or 0
        p_tl    = prev["total_liabilities"] or 0
        p_ca    = prev["current_assets"] or 0
        p_cl    = prev["current_liabilities"] or 0
        p_sec   = prev["securities"] or 0
        p_ltd   = prev["long_term_debt"] or 0

        # 8 Beneish variables
        # Ratios default to 1.0 (neutral, "no change") when the underlying
        # data is missing, so absent fields don't drag the M-Score.
        c_dsr = _safe_div(c_recv, c_sales)
        p_dsr = _safe_div(p_recv, p_sales)
        dsri  = _safe_div(c_dsr, p_dsr, default=1.0) if p_dsr > 0 else 1.0

        c_gm = _safe_div(c_sales - c_cogs, c_sales)
        p_gm = _safe_div(p_sales - p_cogs, p_sales)
        gmi  = _safe_div(p_gm, c_gm) if c_gm > 0 else 1.0

        c_aq = 1 - _safe_div(c_ca + c_ppe + c_sec, c_ta)
        p_aq = 1 - _safe_div(p_ca + p_ppe + p_sec, p_ta)
        aqi  = _safe_div(c_aq, p_aq) if p_aq != 0 else 1.0

        sgi  = _safe_div(c_sales, p_sales)

        c_dep_rate = _safe_div(c_dep, c_dep + c_ppe)
        p_dep_rate = _safe_div(p_dep, p_dep + p_ppe)
        depi = _safe_div(p_dep_rate, c_dep_rate) if c_dep_rate > 0 else 1.0

        c_sga_r = _safe_div(c_sga, c_sales)
        p_sga_r = _safe_div(p_sga, p_sales)
        sgai    = _safe_div(c_sga_r, p_sga_r, default=1.0) if p_sga_r > 0 else 1.0

        # TATA = (Net Income - FFO) / Total Assets
        tata = _safe_div(c_ni - c_ffo, c_ta)

        # LVGI — leverage = (current liabilities + long-term debt) / total assets
        c_lev = _safe_div(c_cl + c_ltd, c_ta)
        p_lev = _safe_div(p_cl + p_ltd, p_ta)
        lvgi  = _safe_div(c_lev, p_lev, default=1.0) if p_lev > 0 else 1.0

        components = {
            "DSRI": round(dsri, 4),
            "GMI":  round(gmi,  4),
            "AQI":  round(aqi,  4),
            "SGI":  round(sgi,  4),
            "DEPI": round(depi, 4),
            "SGAI": round(sgai, 4),
            "TATA": round(tata, 4),
            "LVGI": round(lvgi, 4),
        }

        m_score = (
            BENEISH_COEFS["intercept"]
            + BENEISH_COEFS["DSRI"]  * dsri
            + BENEISH_COEFS["GMI"]   * gmi
            + BENEISH_COEFS["AQI"]   * aqi
            + BENEISH_COEFS["SGI"]   * sgi
            + BENEISH_COEFS["DEPI"]  * depi
            + BENEISH_COEFS["SGAI"]  * sgai
            + BENEISH_COEFS["TATA"]  * tata
            + BENEISH_COEFS["LVGI"]  * lvgi
        )

        is_manip = m_score > BENEISH_THRESHOLD
        verdict  = (
            "LIKELY MANIPULATOR"   if m_score > -1.78 else
            "GREY ZONE"            if m_score > -2.22 else
            "LIKELY NON-MANIPULATOR"
        )

        return BeneishResult(
            m_score=round(m_score, 4),
            verdict=verdict,
            is_manipulator=is_manip,
            components=components
        )

    except Exception as e:
        return BeneishResult(
            m_score=0.0,
            verdict=f"ERROR: {e}",
            is_manipulator=False,
            components={}
        )


# ── Engine 3: IsolationForest ─────────────────────────────────────────────────

def run_isoforest(all_rows, target_borrower_id, target_fy):
    """
    Fit IsolationForest on all available portfolio data.
    Score the specific borrower-year.
    Requires at least 10 rows across all borrowers.
    """
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        # Build feature matrix
        records = []
        target_idx = None

        for i, r in enumerate(all_rows):
            sales = r["sales"] or 0
            if sales <= 0:
                continue

            features = [
                _safe_div(r["total_liabilities"] or 0, r["total_assets"] or 1),
                _safe_div(r["current_assets"] or 0, r["current_liabilities"] or 1),
                _safe_div(r["net_income"] or 0, r["total_assets"] or 1),
                _safe_div(r["ffo"] or 0, sales),
                _safe_div(r["ebitda"] or 0, sales),
            ]
            records.append(features)

            if (r["borrower_id"] == target_borrower_id
                    and r["fy_end"] == target_fy):
                target_idx = len(records) - 1

        if len(records) < 10 or target_idx is None:
            return IsoForestResult(
                score=0.0,
                is_anomaly=False,
                verdict=f"INSUFFICIENT DATA ({len(records)} rows — need 10+)"
            )

        X = StandardScaler().fit_transform(records)

        iso = IsolationForest(
            n_estimators=200,
            contamination=0.05,
            random_state=42
        )
        iso.fit(X)

        score     = float(iso.decision_function([X[target_idx]])[0])
        is_anomaly = iso.predict([X[target_idx]])[0] == -1
        verdict   = "ANOMALY FLAGGED" if is_anomaly else "NORMAL"

        return IsoForestResult(
            score=round(score, 4),
            is_anomaly=is_anomaly,
            verdict=verdict
        )

    except Exception as e:
        return IsoForestResult(
            score=0.0,
            is_anomaly=False,
            verdict=f"ERROR: {e}"
        )


# ── Master runner ─────────────────────────────────────────────────────────────

def run_all_anomaly(borrower_name=None):
    """
    Run all three anomaly engines for the most recent borrower.
    Returns AnomalyReport.
    """
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        # Get borrower
        if borrower_name:
            borrower = conn.execute(
                "SELECT * FROM borrower WHERE name = ?", (borrower_name,)
            ).fetchone()
        else:
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not borrower:
            return AnomalyReport(
                borrower_name="NOT FOUND",
                fy_end="—",
                master_verdict="NO BORROWER IN DATABASE"
            )

        borrower_id = borrower["borrower_id"]

        # Get this borrower's financials (all years, oldest first)
        rows = conn.execute(
            """SELECT * FROM financials
               WHERE borrower_id = ?
               ORDER BY fy_end ASC""",
            (borrower_id,)
        ).fetchall()

        if len(rows) < 2:
            return AnomalyReport(
                borrower_name=borrower["name"],
                fy_end="—",
                master_verdict="NEED AT LEAST 2 YEARS OF DATA"
            )

        latest = rows[-1]
        prior  = rows[-2]

        # Get ALL financials across portfolio (for IsolationForest)
        all_rows = conn.execute(
            "SELECT * FROM financials"
        ).fetchall()

    # Run engines
    report = AnomalyReport(
        borrower_name=borrower["name"],
        fy_end=latest["fy_end"]
    )

    report.z_scores  = run_rolling_z(rows)
    report.beneish   = run_beneish(latest, prior)
    report.isoforest = run_isoforest(
        all_rows,
        target_borrower_id=borrower_id,
        target_fy=latest["fy_end"]
    )

    # Master verdict
    flags = 0
    if any(z.verdict in ("CRITICAL", "WARNING") for z in report.z_scores):
        flags += 1
    if report.beneish and report.beneish.is_manipulator:
        flags += 1
    if report.isoforest and report.isoforest.is_anomaly:
        flags += 1

    report.master_verdict = (
        "HIGH RISK — Multiple anomaly engines flagged"  if flags >= 2 else
        "MODERATE — One engine flagged — investigate"   if flags == 1 else
        "CLEAN — No anomalies detected"
    )

    return report


def save_anomaly_to_db(report):
    """Save anomaly results to anomaly_result table."""
    with sqlite3.connect(DB) as conn:
        # Save Beneish
        if report.beneish:
            conn.execute("""
                INSERT OR REPLACE INTO anomaly_result
                (borrower_id, detector, score, threshold, is_flagged, notes)
                SELECT b.borrower_id, 'beneish_m', ?, ?, ?, ?
                FROM borrower b WHERE b.name = ?
            """, (
                report.beneish.m_score,
                BENEISH_THRESHOLD,
                1 if report.beneish.is_manipulator else 0,
                report.beneish.verdict,
                report.borrower_name
            ))

        # Save IsoForest
        if report.isoforest:
            conn.execute("""
                INSERT OR REPLACE INTO anomaly_result
                (borrower_id, detector, score, threshold, is_flagged, notes)
                SELECT b.borrower_id, 'isolation_forest', ?, ?, ?, ?
                FROM borrower b WHERE b.name = ?
            """, (
                report.isoforest.score,
                0.0,
                1 if report.isoforest.is_anomaly else 0,
                report.isoforest.verdict,
                report.borrower_name
            ))