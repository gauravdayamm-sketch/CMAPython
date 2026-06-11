"""
Multi-model distress score ensemble.

Three independent models, three different vintages and functional forms:
  * Ohlson O-Score (1980)        — logit, 9 vars     (analytics/ohlson.py)
  * Altman Z″ (1995, EM version) — discriminant, 4 vars — built for
                                   emerging-market non-manufacturers too
  * Zmijewski X-Score (1984)     — probit-style logit, 3 vars

No single 1970s-80s US-calibrated model should decide an Indian credit
file; agreement across all three is the signal. The ensemble counts how
many models flag distress and reports per-model reason codes.

Inputs come from the ingested CMA lines when available (retained earnings
known exactly); otherwise from the canonical financials row, where retained
earnings fall back to total net worth with an explicit approximation note.
"""
import math
import sqlite3
import pathlib
from dataclasses import dataclass, field

from analytics.ohlson import compute_ohlson

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


@dataclass
class ScoreResult:
    model:   str
    score:   float
    zone:    str            # SAFE | GREY | DISTRESS
    prob:    float = None   # where the model yields a probability
    components: dict = field(default_factory=dict)
    note:    str = ""


@dataclass
class EnsembleResult:
    borrower_name: str
    fy_end:        str = ""
    basis:         str = "audited"
    ohlson:        ScoreResult = None
    altman:        ScoreResult = None
    zmijewski:     ScoreResult = None
    models_flagging: int = 0
    verdict:       str = ""
    error:         str = ""

    def as_dict(self):
        def sr(s):
            return None if s is None else {
                "model": s.model, "score": s.score, "zone": s.zone,
                "prob": s.prob, "components": s.components, "note": s.note,
            }
        return {
            "borrower": self.borrower_name, "fy_end": self.fy_end,
            "basis": self.basis,
            "ohlson": sr(self.ohlson), "altman": sr(self.altman),
            "zmijewski": sr(self.zmijewski),
            "models_flagging": self.models_flagging,
            "verdict": self.verdict, "error": self.error,
        }


# ── Altman Z″ (1995) ─────────────────────────────────────────────────────────

def altman_z2(ta, wc, retained_earnings, ebit, tnw, tl, note=""):
    """Z″ = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4 (book equity variant)."""
    if ta <= 0 or tl <= 0:
        return None
    x1 = wc / ta
    x2 = retained_earnings / ta
    x3 = ebit / ta
    x4 = tnw / tl
    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
    zone = "SAFE" if z > 2.6 else "GREY" if z >= 1.1 else "DISTRESS"
    return ScoreResult(
        model="Altman Z″ (1995)",
        score=round(z, 4), zone=zone,
        components={"WC/TA": round(x1, 4), "RE/TA": round(x2, 4),
                    "EBIT/TA": round(x3, 4), "TNW/TL": round(x4, 4)},
        note=note,
    )


# ── Zmijewski X-Score (1984) ──────────────────────────────────────────────────

def zmijewski_x(ta, tl, ni, ca, cl):
    """X = -4.336 - 4.513·ROA + 5.679·(TL/TA) + 0.004·(CA/CL)."""
    if ta <= 0:
        return None
    roa  = ni / ta
    lev  = tl / ta
    liq  = ca / cl if cl > 0 else 0.0
    x    = -4.336 - 4.513 * roa + 5.679 * lev + 0.004 * liq
    prob = 1.0 / (1.0 + math.exp(-x))
    zone = "DISTRESS" if prob > 0.50 else "GREY" if prob > 0.30 else "SAFE"
    return ScoreResult(
        model="Zmijewski X (1984)",
        score=round(x, 4), zone=zone, prob=round(prob, 4),
        components={"ROA": round(roa, 4), "TL/TA": round(lev, 4),
                    "CA/CL": round(liq, 4)},
    )


# ── Input loading ─────────────────────────────────────────────────────────────

def _inputs_from_cma(conn, borrower_id):
    row = conn.execute("""
        SELECT s.stmt_id, s.fy_end FROM cma_statement s
        WHERE s.borrower_id = ? AND s.statement_type = 'audited'
          AND s.is_dual = 0
        ORDER BY s.col_index DESC LIMIT 1
    """, (borrower_id,)).fetchone()
    basis_note = ""
    if not row:
        # New unit: score the nearest estimated/projected year, clearly
        # labelled — indicative only, these are the borrower's own numbers.
        row = conn.execute("""
            SELECT s.stmt_id, s.fy_end, s.statement_type, s.fy_label
            FROM cma_statement s
            WHERE s.borrower_id = ? AND s.is_dual = 0
              AND s.statement_type IN ('estimated', 'projected')
            ORDER BY s.col_index ASC LIMIT 1
        """, (borrower_id,)).fetchone()
        if not row:
            return None
        basis_note = (f"scored on {row['statement_type'].upper()} "
                      f"{row['fy_label']} — new unit, indicative only")
    L = dict(conn.execute(
        "SELECT line_code, value FROM cma_line WHERE stmt_id = ?",
        (row["stmt_id"],)).fetchall())
    prior = conn.execute("""
        SELECT s.stmt_id FROM cma_statement s
        WHERE s.borrower_id = ? AND s.statement_type = 'audited'
          AND s.is_dual = 0
        ORDER BY s.col_index DESC LIMIT 1 OFFSET 1
    """, (borrower_id,)).fetchone()
    ni_prev = None
    if prior:
        ni_prev = dict(conn.execute(
            "SELECT line_code, value FROM cma_line WHERE stmt_id = ?",
            (prior["stmt_id"],)).fetchall()).get("os_pat")

    # True retained-earnings reserves: P&L surplus + free reserves
    re_ = L["bs_general_reserve"] + L["bs_pl_surplus"] + L["bs_other_reserves"]
    return {
        "fy_end": row["fy_end"],
        "ta": L["bs_total_assets"], "tl": L["bs_tol"],
        "ca": L["bs_tca"], "cl": L["bs_tcl"],
        "wc": L["bs_tca"] - L["bs_tcl"],
        "ni": L["os_pat"], "ni_prev": ni_prev,
        "ffo": L["os_cash_accruals"], "ebit": L["os_opbi"],
        "tnw": L["bs_tnw"], "retained_earnings": re_,
        "re_note": "", "basis_note": basis_note,
    }


def _inputs_from_financials(conn, borrower_id):
    rows = conn.execute("""
        SELECT * FROM financials WHERE borrower_id = ?
        ORDER BY fy_end DESC LIMIT 2
    """, (borrower_id,)).fetchall()
    if not rows:
        return None
    f = rows[0]
    tnw = f["tnw"] if f["tnw"] else (f["total_assets"] or 0) - (f["total_liabilities"] or 0)
    return {
        "fy_end": f["fy_end"],
        "ta": f["total_assets"] or 0, "tl": f["total_liabilities"] or 0,
        "ca": f["current_assets"] or 0, "cl": f["current_liabilities"] or 0,
        "wc": f["working_capital"] or 0,
        "ni": f["net_income"] or 0,
        "ni_prev": rows[1]["net_income"] if len(rows) > 1 else None,
        "ffo": f["ffo"] or 0,
        "ebit": f["ebit"] if f["ebit"] else (f["ebitda"] or 0) - (f["depreciation"] or 0),
        "tnw": tnw,
        "retained_earnings": tnw,
        "re_note": ("RE approximated by TNW — reserve breakdown not "
                    "available without CMA ingestion"),
        "basis_note": "",
    }


# ── Ensemble ──────────────────────────────────────────────────────────────────

def run_ensemble(borrower_name=None, db_path=None):
    """Run all three models on the latest audited year."""
    with sqlite3.connect(db_path or DB) as conn:
        conn.row_factory = sqlite3.Row
        if borrower_name:
            borrower = conn.execute(
                "SELECT * FROM borrower WHERE name = ?", (borrower_name,)
            ).fetchone()
        else:
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not borrower:
            return EnsembleResult(borrower_name=borrower_name or "NOT FOUND",
                                  error="No borrower found")
        inputs = (_inputs_from_cma(conn, borrower["borrower_id"])
                  or _inputs_from_financials(conn, borrower["borrower_id"]))

    if not inputs or inputs["ta"] <= 0:
        return EnsembleResult(borrower_name=borrower["name"],
                              error="No usable financial data")

    result = EnsembleResult(borrower_name=borrower["name"],
                            fy_end=inputs["fy_end"],
                            basis=inputs.get("basis_note") or "audited")

    o = compute_ohlson(
        ta=inputs["ta"], tl=inputs["tl"], ca=inputs["ca"], cl=inputs["cl"],
        wc=inputs["wc"], ni=inputs["ni"], ffo=inputs["ffo"],
        ni_prev=inputs["ni_prev"],
    )
    if o:
        zone = ("DISTRESS" if o.prob_default >= 0.50 else
                "GREY" if o.prob_default >= 0.20 else "SAFE")
        result.ohlson = ScoreResult(
            model="Ohlson O (1980)", score=o.o_score, zone=zone,
            prob=o.prob_default, components=o.contributions,
        )

    result.altman = altman_z2(
        ta=inputs["ta"], wc=inputs["wc"],
        retained_earnings=inputs["retained_earnings"],
        ebit=inputs["ebit"], tnw=inputs["tnw"], tl=inputs["tl"],
        note=inputs["re_note"],
    )
    result.zmijewski = zmijewski_x(
        ta=inputs["ta"], tl=inputs["tl"], ni=inputs["ni"],
        ca=inputs["ca"], cl=inputs["cl"],
    )

    models = [m for m in (result.ohlson, result.altman, result.zmijewski) if m]
    result.models_flagging = sum(1 for m in models if m.zone == "DISTRESS")
    grey = sum(1 for m in models if m.zone == "GREY")

    if result.models_flagging >= 2:
        result.verdict = "DISTRESS CONSENSUS — multiple models flag default risk"
    elif result.models_flagging == 1:
        result.verdict = "MIXED — one model flags distress; investigate drivers"
    elif grey >= 2:
        result.verdict = "WATCH — multiple models in the grey zone"
    else:
        result.verdict = "SOUND — no model signals distress"
    return result
