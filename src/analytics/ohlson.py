"""
Ohlson O-Score (1980) - Probability of Default Model
Canonical 10-term logistic regression for credit default prediction.
"""
import math
import sqlite3
import pathlib
from dataclasses import dataclass

# Database path
DB = pathlib.Path(__file__).resolve().parents[2] / "db" / "cma.sqlite"

# Ohlson 1980 coefficients
# [intercept, SIZE, TLTA, WCTA, CLCA, NITA, FFOTL, INTWO, OENEG, CHIN]
COEFFICIENTS = [-1.32, -0.407, 6.03, -1.43, 0.076, -2.37, -1.83, 0.285, -1.72, -0.521]

# Indian corporate size deflator (median Total Assets in Cr)
# Update this via LLM bridge quarterly
SIZE_DEFLATOR = 200.0

@dataclass
class OhlsonResult:
    o_score: float
    prob_default: float
    risk_band: str
    contributions: dict
    inputs: dict

def compute_ohlson(
    ta, tl, ca, cl, wc, ni, ffo,
    ni_prev=None,
    deflator=SIZE_DEFLATOR
):
    """
    Compute Ohlson O-Score from financial inputs.
    
    Parameters:
        ta  = Total Assets
        tl  = Total Liabilities
        ca  = Current Assets
        cl  = Current Liabilities
        wc  = Working Capital (ca - cl)
        ni  = Net Income (PAT)
        ffo = Funds From Operations (PAT + Depreciation)
        ni_prev = Prior year Net Income (for INTWO and CHIN)
        deflator = Size scaling factor
    """

    # Avoid division by zero
    if ta <= 0:
        return None

    # Calculate the 9 variables
    size  = math.log(ta / deflator) if ta > 0 else 0
    tlta  = tl / ta
    wcta  = wc / ta
    clca  = cl / ca if ca > 0 else 0
    nita  = ni / ta
    ffotl = ffo / tl if tl > 0 else 0

    # Binary flags
    oeneg = 1 if tl > ta else 0
    intwo = 1 if (ni < 0 and ni_prev is not None and ni_prev < 0) else 0

    # CHIN - earnings change indicator
    if ni_prev is None or (abs(ni) + abs(ni_prev)) == 0:
        chin = 0.0
    else:
        chin = (ni - ni_prev) / (abs(ni) + abs(ni_prev))

    # Feature vector (intercept first)
    features = [1, size, tlta, wcta, clca, nita, ffotl, intwo, oeneg, chin]
    labels   = ["intercept", "SIZE", "TLTA", "WCTA", "CLCA",
                 "NITA", "FFOTL", "INTWO", "OENEG", "CHIN"]

    # Logit = dot product of coefficients and features
    o_score = sum(c * f for c, f in zip(COEFFICIENTS, features))

    # Probability via logistic function
    prob = 1.0 / (1.0 + math.exp(-o_score))

    # Risk band
    if prob < 0.038:
        band = "LOW - Healthy"
    elif prob < 0.20:
        band = "WATCH"
    elif prob < 0.50:
        band = "ELEVATED"
    else:
        band = "SEVERE"

    # Contribution of each variable to the o_score
    contributions = {
        label: round(coef * feat, 4)
        for label, coef, feat in zip(labels, COEFFICIENTS, features)
    }

    return OhlsonResult(
        o_score=round(o_score, 4),
        prob_default=round(prob, 4),
        risk_band=band,
        contributions=contributions,
        inputs={
            "ta": ta, "tl": tl, "ca": ca, "cl": cl,
            "wc": wc, "ni": ni, "ffo": ffo,
            "SIZE": round(size, 4),
            "OENEG": oeneg, "INTWO": intwo, "CHIN": round(chin, 4)
        }
    )

def compute_ohlson_from_dict(d):
    """Wrapper that accepts a dictionary of inputs - used by excel_io.py"""
    result = compute_ohlson(
        ta      = d.get("total_assets", 0),
        tl      = d.get("total_liabilities", 0),
        ca      = d.get("current_assets", 0),
        cl      = d.get("current_liabilities", 0),
        wc      = d.get("working_capital", 0),
        ni      = d.get("net_income", 0),
        ffo     = d.get("ffo", 0),
        ni_prev = d.get("net_income_prev", None),
    )
    if result is None:
        return {"error": "Total Assets must be greater than zero"}
    return {
        "o_score":      result.o_score,
        "prob_default": result.prob_default,
        "risk_band":    result.risk_band,
        "contributions": result.contributions
    }

def save_to_db(borrower_id, fy_end, result):
    """Save Ohlson result to anomaly_result table in SQLite"""
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO anomaly_result
            (borrower_id, detector, score, threshold, is_flagged, notes)
            VALUES (?, 'ohlson_pd', ?, 0.038, ?, ?)
        """, (
            borrower_id,
            result.prob_default,
            1 if result.prob_default >= 0.038 else 0,
            f"O-Score: {result.o_score} | Band: {result.risk_band}"
        ))