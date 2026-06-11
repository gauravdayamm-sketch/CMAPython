"""
Working Capital Assessment Engine — the CMA Form V math, in Python.

Computes, for every year-column ingested from the CMA workbook:
  * MPBF under Tandon Method I and Method II (canonical Form V steps)
  * Assessed Bank Finance (ABF = WCG − actual NWC, the workbook's row)
  * Nayak turnover-method comparison
  * Gross / Net DSCR per year + average gross DSCR
  * Holding levels (inventory / DSO / DPO / cash conversion cycle)
  * Ratio suite with threshold verdicts
  * Red flags: NWC erosion, DSO & inventory-days drift, sales-growth floor,
    and estimated-vs-audited variance on the dual column (projection
    credibility — did last year's estimate survive the audit?)

Thresholds come from the borrower's ingested 0_Setup values, falling back
to the template defaults.
"""
import sqlite3
import pathlib
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"

DEFAULT_THRESHOLDS = {
    "min_current_ratio":     1.17,
    "min_dscr_any":          1.25,
    "min_dscr_avg":          1.50,
    "max_tol_tnw":           4.00,
    "max_debt_ebitda":       4.50,
    "min_icr":               1.50,
    "sales_growth_floor":   -0.05,
    "min_facr":              1.10,
    "max_nwc_erosion":      -0.30,
    "max_inv_days_increase": 0.40,
    "max_dso_increase":      0.30,
    "max_dpo_stretch":       0.50,
}


@dataclass
class WCAssessment:
    borrower_name: str
    years:         list = field(default_factory=list)   # per-year metric dicts
    thresholds:    dict = field(default_factory=dict)
    proposals:     dict = field(default_factory=dict)
    dscr_avg_gross: float = None
    red_flags:     list = field(default_factory=list)
    verdicts:      list = field(default_factory=list)   # threshold checks
    error:         str  = ""

    @property
    def latest_audited(self):
        rows = [y for y in self.years
                if y["statement_type"] == "audited" and not y["is_dual"]]
        return rows[-1] if rows else None

    @property
    def assessment_basis(self):
        """(year, label) the assessment anchors on. Audited actuals when
        they exist; for new units the current estimate, else the FIRST
        projected year (the nearest, most credible projection)."""
        if self.latest_audited:
            return self.latest_audited, "audited"
        est = [y for y in self.years
               if y["statement_type"] == "estimated" and not y["is_dual"]]
        if est:
            return est[0], "ESTIMATED — new unit, no audited history"
        proj = [y for y in self.years if y["statement_type"] == "projected"]
        if proj:
            return proj[0], "PROJECTED — new unit, no audited history"
        return None, ""

    def year(self, fy_label, statement_type=None):
        for y in self.years:
            if y["fy_label"] == fy_label and (
                    statement_type is None
                    or y["statement_type"] == statement_type):
                return y
        return None


def _div(a, b):
    return a / b if b not in (0, None) and a is not None else None


def _load(borrower_name, db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if borrower_name:
            borrower = conn.execute(
                "SELECT * FROM borrower WHERE name = ?", (borrower_name,)
            ).fetchone()
        else:
            borrower = conn.execute("""
                SELECT b.* FROM borrower b
                JOIN cma_statement s ON s.borrower_id = b.borrower_id
                GROUP BY b.borrower_id ORDER BY MAX(s.imported_at) DESC LIMIT 1
            """).fetchone()
        if not borrower:
            return None, [], {}, {}

        bid = borrower["borrower_id"]
        stmts = conn.execute("""
            SELECT * FROM cma_statement WHERE borrower_id = ?
            ORDER BY col_index ASC
        """, (bid,)).fetchall()

        years = []
        for s in stmts:
            lines = dict(conn.execute(
                "SELECT line_code, value FROM cma_line WHERE stmt_id = ?",
                (s["stmt_id"],)
            ).fetchall())
            years.append({
                "fy_label":       s["fy_label"],
                "fy_end":         s["fy_end"],
                "statement_type": s["statement_type"],
                "is_dual":        bool(s["is_dual"]),
                "col_index":      s["col_index"],
                "lines":          lines,
            })

        thresholds = dict(DEFAULT_THRESHOLDS)
        thresholds.update({
            r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM cma_threshold WHERE borrower_id = ?", (bid,))
        })
        proposals = {
            r["facility"]: {"existing": r["existing"], "proposed": r["proposed"]}
            for r in conn.execute(
                "SELECT * FROM cma_proposal WHERE borrower_id = ?", (bid,))
        }
    return borrower, years, thresholds, proposals


# ── Per-year computations ─────────────────────────────────────────────────────

def compute_year_metrics(L):
    """All Form V / DSCR / holding / ratio metrics for one year's lines."""
    tca, tcl = L["bs_tca"], L["bs_tcl"]
    ocl      = L["bs_ocl_total"]
    wcg      = tca - ocl
    nwc      = tca - tcl

    # Form V (Tandon): MPBF = WCG − max(stipulated margin, actual NWC)
    min_nwc_1 = 0.25 * wcg
    min_nwc_2 = 0.25 * tca
    mpbf_1    = wcg - max(min_nwc_1, nwc)
    mpbf_2    = wcg - max(min_nwc_2, nwc)
    abf       = wcg - nwc                      # workbook row 12

    # Nayak turnover method (for limits up to ₹5 Cr)
    net_sales   = L["os_net_sales"]
    turnover_wc = 0.25 * net_sales
    turnover_bf = 0.20 * net_sales

    # DSCR
    nca    = L["os_cash_accruals"] - L["ds_accruals_used"]
    inst   = L["ds_tl_inst_applicant"] + L["ds_tl_inst_other"]
    int_tl = L["os_interest_tl"]
    gross_dscr = _div(nca + int_tl, inst + int_tl)
    net_dscr   = _div(nca, inst)

    # Holding levels. DSO divides by NET sales — the bank's LLMS system
    # convention (verified against its Performance exports).
    purchases = L["os_total_rm"] + L["os_total_spares"]
    inv_days  = _div(L["bs_inventory_total"] * 365, net_sales)
    dso       = _div((L["bs_receivables_domestic"] + L["bs_receivables_export"]) * 365,
                     net_sales)
    dpo       = _div((L["bs_creditors_trade"] + L["bs_creditors_lc"]) * 365, purchases)
    ccc       = (inv_days or 0) + (dso or 0) - (dpo or 0) \
                if None not in (inv_days, dso) else None

    term_debt = L["bs_debentures"] + L["bs_secured_tl"] + L["bs_dpc"]

    return {
        # Form V
        "tca": tca, "ocl": ocl, "wcg": round(wcg, 2),
        "nwc": round(nwc, 2),
        "min_nwc_method1": round(min_nwc_1, 2),
        "min_nwc_method2": round(min_nwc_2, 2),
        "mpbf_method1": round(mpbf_1, 2),
        "mpbf_method2": round(mpbf_2, 2),
        "abf": round(abf, 2),
        "margin_ok_method2": nwc >= min_nwc_2,
        # Turnover method
        "turnover_wc_requirement": round(turnover_wc, 2),
        "turnover_bank_finance":   round(turnover_bf, 2),
        # DSCR
        "nca": round(nca, 2), "tl_instalments": round(inst, 2),
        "interest_tl": round(int_tl, 2),
        "gross_dscr": round(gross_dscr, 4) if gross_dscr is not None else None,
        "net_dscr":   round(net_dscr, 4) if net_dscr is not None else None,
        # Holding levels
        "inventory_days": round(inv_days, 1) if inv_days is not None else None,
        "dso": round(dso, 1) if dso is not None else None,
        "dpo": round(dpo, 1) if dpo is not None else None,
        "cash_conversion_cycle": round(ccc, 1) if ccc is not None else None,
        # Ratios
        "net_sales": net_sales,
        "current_ratio": _r4(_div(tca, tcl)),
        "quick_ratio":   _r4(_div(tca - L["bs_inventory_total"], tcl)),
        "tol_tnw":       _r4(_div(L["bs_tol"], L["bs_tnw"])),
        "ttl_tnw":       _r4(_div(L["bs_ttl"], L["bs_tnw"])),
        "debt_ebitda":   _r4(_div(L["bs_ttl"], L["os_ebitda"])),
        "icr":           _r4(_div(L["os_opbi"], L["os_total_interest"])),
        "opm":           _r4(_div(L["os_opbi"], net_sales)),
        "pat_margin":    _r4(_div(L["os_pat"], net_sales)),
        "cash_margin":   _r4(_div(L["os_cash_accruals"], net_sales)),
        "roce":          _r4(_div(L["os_opbi"], L["bs_tnw"] + L["bs_ttl"])),
        "facr":          _r4(_div(L["bs_net_block"], term_debt)),
        "tnw": L["bs_tnw"], "tol": L["bs_tol"],
        "pat": L["os_pat"], "ebitda": L["os_ebitda"],
    }


def _r4(v):
    return round(v, 4) if v is not None else None


def _pct_change(curr, prev):
    if curr is None or prev in (None, 0):
        return None
    return (curr - prev) / abs(prev)


# ── Threshold verdicts and red flags ──────────────────────────────────────────

CHECKS = [
    # metric key        threshold key         direction  label
    ("current_ratio",  "min_current_ratio",   ">=", "Current Ratio"),
    ("gross_dscr",     "min_dscr_any",        ">=", "Gross DSCR"),
    ("tol_tnw",        "max_tol_tnw",         "<=", "TOL/TNW"),
    ("debt_ebitda",    "max_debt_ebitda",     "<=", "Debt/EBITDA"),
    ("icr",            "min_icr",             ">=", "Interest Coverage"),
    ("facr",           "min_facr",            ">=", "FACR"),
]


def _run_verdicts(years, thresholds):
    verdicts = []
    for y in years:
        if y["statement_type"] == "estimated" and y["is_dual"]:
            continue                       # superseded by the audited twin
        m = y["metrics"]
        for metric, tkey, op, label in CHECKS:
            value, limit = m.get(metric), thresholds[tkey]
            if value is None:
                continue
            ok = value >= limit if op == ">=" else value <= limit
            verdicts.append({
                "fy_label": y["fy_label"],
                "statement_type": y["statement_type"],
                "metric": label, "value": value,
                "threshold": limit, "direction": op,
                "status": "OK" if ok else "BREACH",
            })
    return verdicts


def _run_red_flags(years, thresholds):
    flags = []
    audited = [y for y in years
               if y["statement_type"] == "audited" and not y["is_dual"]]
    if len(audited) >= 2:
        latest, prior = audited[-1]["metrics"], audited[-2]["metrics"]
        fy = audited[-1]["fy_label"]

        growth = _pct_change(latest["net_sales"], prior["net_sales"])
        if growth is not None and growth < thresholds["sales_growth_floor"]:
            flags.append(f"{fy}: net sales fell {growth:.1%} — below the "
                         f"{thresholds['sales_growth_floor']:.0%} floor")

        nwc_move = _pct_change(latest["nwc"], prior["nwc"])
        if nwc_move is not None and nwc_move < thresholds["max_nwc_erosion"]:
            flags.append(f"{fy}: NWC eroded {nwc_move:.1%} vs prior year "
                         f"(tolerance {thresholds['max_nwc_erosion']:.0%})")

        for key, tkey, label in (
            ("dso", "max_dso_increase", "DSO"),
            ("inventory_days", "max_inv_days_increase", "Inventory days"),
            ("dpo", "max_dpo_stretch", "DPO (creditor stretch)"),
        ):
            move = _pct_change(latest[key], prior[key])
            if move is not None and move > thresholds[tkey]:
                flags.append(f"{fy}: {label} rose {move:.0%} "
                             f"({prior[key]:.0f} → {latest[key]:.0f} days)")

    # Dual-column credibility: estimate vs audited actuals, same FY
    if audited:
        fy = audited[-1]["fy_label"]
        dual = next((y for y in years if y["is_dual"] and y["fy_label"] == fy), None)
        if dual:
            est, act = dual["metrics"], audited[-1]["metrics"]
            for key, label in (("net_sales", "Net sales"), ("pat", "PAT")):
                var = _pct_change(act[key], est[key])
                if var is not None and abs(var) > 0.10:
                    flags.append(
                        f"{fy}: {label} actuals deviated {var:+.1%} from the "
                        f"borrower's own estimate — projection credibility risk"
                    )
    return flags


# ── Entry point ───────────────────────────────────────────────────────────────

def assess_working_capital(borrower_name=None, db_path=None):
    """Run the full WC assessment for a borrower with ingested CMA data."""
    borrower, raw_years, thresholds, proposals = _load(
        borrower_name, db_path or DB)

    if not borrower:
        return WCAssessment(borrower_name=borrower_name or "NOT FOUND",
                            error="No borrower with CMA data found")
    if not raw_years:
        return WCAssessment(borrower_name=borrower["name"],
                            error="No CMA statements ingested for this borrower")

    result = WCAssessment(
        borrower_name=borrower["name"],
        thresholds=thresholds,
        proposals=proposals,
    )

    for y in raw_years:
        result.years.append({
            "fy_label":       y["fy_label"],
            "fy_end":         y["fy_end"],
            "statement_type": y["statement_type"],
            "is_dual":        y["is_dual"],
            "metrics":        compute_year_metrics(y["lines"]),
        })

    # Average gross DSCR over years that actually service term debt
    dscrs = [y["metrics"]["gross_dscr"] for y in result.years
             if y["metrics"]["tl_instalments"] > 0
             and y["metrics"]["gross_dscr"] is not None
             and not (y["is_dual"] and y["statement_type"] == "estimated")]
    if dscrs:
        result.dscr_avg_gross = round(sum(dscrs) / len(dscrs), 4)

    result.verdicts  = _run_verdicts(result.years, thresholds)
    result.red_flags = _run_red_flags(result.years, thresholds)
    return result


def form_v(assessment, fy_label=None):
    """Canonical CMA Form V rows for one year (default: assessment basis)."""
    if fy_label:
        y = assessment.year(fy_label, "audited") or assessment.year(fy_label)
    else:
        y, _ = assessment.assessment_basis
    if not y:
        return []
    m = y["metrics"]
    return [
        ("1. Total Current Assets (TCA)",                  m["tca"]),
        ("2. Current Liabilities other than bank (OCL)",   m["ocl"]),
        ("3. Working Capital Gap (1 − 2)",                 m["wcg"]),
        ("4. Min. stipulated NWC — 25% of WCG (Method I)", m["min_nwc_method1"]),
        ("5. Min. stipulated NWC — 25% of TCA (Method II)", m["min_nwc_method2"]),
        ("6. Actual / Projected NWC",                      m["nwc"]),
        ("7. MPBF — Method I  (3 − max(4, 6))",            m["mpbf_method1"]),
        ("8. MPBF — Method II (3 − max(5, 6))",            m["mpbf_method2"]),
        ("9. ABF = WCG − NWC (workbook basis)",            m["abf"]),
    ]
