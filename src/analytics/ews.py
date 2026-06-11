"""
Early Warning Signals engine — ports the workbook's three rule sheets:

  1. run_red_flags()        : the 32 quantitative tests of 12_Red_Flags
  2. run_exception_engine() : the 13 deep-diagnostic tests of 12B_Exception_Engine
  3. run_ews()              : 17_Advanced_EWS per the RBI Master Direction on
                              Fraud Risk Management (Jul 2024) — financial-
                              behavior indicators are auto-detected from the
                              ingested CMA data, qualitative ones are passed
                              in as manual triggers; the RFA rule is applied
                              (any Critical, or 2+ High → flag for RFA).

All tests run on ingested cma_statement/cma_line data (latest audited vs
prior audited, plus projections where the rule needs them).
"""
import sqlite3
import pathlib
from dataclasses import dataclass, field

from analytics.wc_assessment import _load, compute_year_metrics

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


@dataclass
class Flag:
    rule_id:   str
    section:   str
    test:      str
    value:     object          # number or short string
    threshold: str
    status:    str             # OK | BREACH | VERIFY | N/A
    detail:    str = ""

    @property
    def triggered(self):
        return self.status not in ("OK", "N/A")


@dataclass
class EWSReport:
    borrower_name: str
    red_flags:        list = field(default_factory=list)
    exceptions:       list = field(default_factory=list)
    ews_indicators:   list = field(default_factory=list)   # dicts
    red_flag_verdict: str  = ""
    basis:            str  = "audited"
    rfa_required:     bool = False
    rfa_reason:       str  = ""
    error:            str  = ""

    @property
    def triggered_red_flags(self):
        return [f for f in self.red_flags if f.triggered]

    @property
    def triggered_exceptions(self):
        return [f for f in self.exceptions if f.triggered]


def _pct(curr, prev):
    if curr is None or prev in (None, 0):
        return None
    return (curr - prev) / abs(prev)


def _setup_meta(borrower_id, db_path):
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute(
            "SELECT key, value FROM cma_setup_meta WHERE borrower_id = ?",
            (borrower_id,)).fetchall())


def _context(borrower_name, db_path):
    """Latest/prior audited lines+metrics, all years, thresholds, meta."""
    borrower, years, thresholds, _ = _load(borrower_name, db_path)
    if not borrower or not years:
        return None
    audited = [y for y in years
               if y["statement_type"] == "audited" and not y["is_dual"]]
    if audited:
        latest, prior, basis = (audited[-1],
                                audited[-2] if len(audited) >= 2 else None,
                                "audited")
    else:
        # New unit: anchor the level-based tests on the current estimate,
        # else the first projection. Trend tests degrade to N/A (no prior).
        est = [y for y in years
               if y["statement_type"] == "estimated" and not y["is_dual"]]
        proj = [y for y in years if y["statement_type"] == "projected"]
        pool = est or proj
        if not pool:
            return None
        latest, prior = pool[0], None
        basis = (f"{pool[0]['statement_type'].upper()} "
                 f"{pool[0]['fy_label']} — new unit, no audited history")
    ctx = {
        "borrower":   borrower,
        "years":      years,
        "audited":    audited,
        "latest":     latest,
        "prior":      prior,
        "basis":      basis,
        "projected":  [y for y in years if y["statement_type"] == "projected"],
        "thresholds": thresholds,
        "meta":       _setup_meta(borrower["borrower_id"], db_path),
    }
    ctx["L"]  = ctx["latest"]["lines"]
    ctx["P"]  = ctx["prior"]["lines"] if ctx["prior"] else None
    ctx["mL"] = compute_year_metrics(ctx["L"])
    ctx["mP"] = compute_year_metrics(ctx["P"]) if ctx["P"] else None
    return ctx


# ── 12_Red_Flags: 32 quantitative tests ───────────────────────────────────────

def run_red_flags(ctx):
    L, P, mL, mP = ctx["L"], ctx["P"], ctx["mL"], ctx["mP"]
    t = ctx["thresholds"]
    flags = []

    def add(rule_id, section, test, value, threshold, breach,
            detail_breach, detail_ok="", status_breach="BREACH",
            needs_prior=False):
        if needs_prior and P is None:
            flags.append(Flag(rule_id, section, test, None, threshold,
                              "N/A", "Needs a prior audited year"))
            return
        status = status_breach if breach else "OK"
        flags.append(Flag(
            rule_id, section, test,
            round(value, 4) if isinstance(value, float) else value,
            threshold, status,
            detail_breach if breach else detail_ok))

    # I. PROFITABILITY
    add("RF1", "Profitability", "Loss in latest audited year",
        L["os_pat"], ">= 0", L["os_pat"] < 0,
        "PAT negative in latest audited year — investigate one-off vs structural.")
    if P:
        opm_move = _pct(mL["opm"], mP["opm"])
        add("RF2", "Profitability", "OPM declining trend",
            opm_move, "stable / improving",
            opm_move is not None and opm_move < -0.10,
            "Operating margin compressed >10% vs prior year.",
            status_breach="VERIFY")
        growth = _pct(L["os_net_sales"], P["os_net_sales"])
        add("RF3", "Profitability", "Sales jump > 50% (verification needed)",
            growth, "<= 50% YoY",
            growth is not None and growth > 0.50,
            "Sales jumped >50% — verify with GST returns / sales register.",
            status_breach="VERIFY")
        add("RF4", "Profitability", "Sales decline YoY",
            growth, f">= {t['sales_growth_floor']:.0%}",
            growth is not None and growth < t["sales_growth_floor"],
            "Sales declined beyond the acceptable floor.")
    else:
        for rid, name in (("RF2", "OPM declining trend"),
                          ("RF3", "Sales jump > 50%"), ("RF4", "Sales decline YoY")):
            flags.append(Flag(rid, "Profitability", name, None, "—", "N/A",
                              "Needs a prior audited year"))

    # II. LIQUIDITY
    add("RF5", "Liquidity", "Current ratio breach",
        mL["current_ratio"], f">= {t['min_current_ratio']}",
        mL["current_ratio"] is not None
        and mL["current_ratio"] < t["min_current_ratio"],
        "Current ratio below sanctioned threshold.")
    add("RF6", "Liquidity", "Negative NWC",
        mL["nwc"], ">= 0", mL["nwc"] < 0,
        "Working capital is negative — short-term funds deployed long.")
    nwc_move = _pct(mL["nwc"], mP["nwc"]) if mP else None
    add("RF7", "Liquidity", "NWC erosion YoY beyond tolerance",
        nwc_move, f">= {t['max_nwc_erosion']:.0%}",
        nwc_move is not None and nwc_move < t["max_nwc_erosion"],
        "NWC eroded beyond tolerance vs prior year.", needs_prior=True)

    # III. LEVERAGE
    add("RF8", "Leverage", "TOL/TNW exceeds threshold",
        mL["tol_tnw"], f"<= {t['max_tol_tnw']}",
        mL["tol_tnw"] is not None and mL["tol_tnw"] > t["max_tol_tnw"],
        "Outside liabilities too high relative to net worth.")
    add("RF9", "Leverage", "Negative or zero TNW",
        L["bs_tnw"], "> 0", L["bs_tnw"] <= 0,
        "Net worth wiped out — equity cushion gone.")
    add("RF10", "Leverage", "Debt/EBITDA breach",
        mL["debt_ebitda"], f"<= {t['max_debt_ebitda']}",
        mL["debt_ebitda"] is not None
        and mL["debt_ebitda"] > t["max_debt_ebitda"],
        "Term debt exceeds the EBITDA multiple cap.")

    # IV. COVERAGE
    add("RF11", "Coverage", "ICR below threshold",
        mL["icr"], f">= {t['min_icr']}",
        mL["icr"] is not None and mL["icr"] < t["min_icr"],
        "Operating profit covers interest too thinly.")
    year_dscrs = [
        (y["fy_label"], compute_year_metrics(y["lines"])["gross_dscr"])
        for y in ctx["years"]
        if not (y["is_dual"] and y["statement_type"] == "estimated")
        and compute_year_metrics(y["lines"])["tl_instalments"] > 0
    ]
    valid = [d for _, d in year_dscrs if d is not None]
    min_dscr = min(valid) if valid else None
    add("RF12", "Coverage", "DSCR breach in any year",
        min_dscr, f">= {t['min_dscr_any']}",
        min_dscr is not None and min_dscr < t["min_dscr_any"],
        "Gross DSCR dips below minimum in at least one year.")
    avg_dscr = round(sum(valid) / len(valid), 4) if valid else None
    add("RF13", "Coverage", "Average DSCR below threshold",
        avg_dscr, f">= {t['min_dscr_avg']}",
        avg_dscr is not None and avg_dscr < t["min_dscr_avg"],
        "Average gross DSCR below threshold across the loan tenor.")

    # V. WORKING CAPITAL & EFFICIENCY
    for rid, key, tkey, label in (
            ("RF14", "inventory_days", "max_inv_days_increase", "Inventory days stretching"),
            ("RF15", "dso", "max_dso_increase", "DSO stretching"),
            ("RF16", "dpo", "max_dpo_stretch", "DPO stretching (creditor squeeze)")):
        move = _pct(mL[key], mP[key]) if mP else None
        add(rid, "Working Capital", label,
            move, f"<= +{t[tkey]:.0%} YoY",
            move is not None and move > t[tkey],
            f"{label} beyond tolerance vs prior year.", needs_prior=True)
    stbb_share = mL["abf"] / mL["tca"] if mL["tca"] else None
    stbb_tcl = (L["bs_stbb_total"] / L["bs_tcl"]) if L["bs_tcl"] else None
    add("RF17", "Working Capital", "STBB > 70% of TCL (bank over-reliance)",
        stbb_tcl, "<= 70%",
        stbb_tcl is not None and stbb_tcl > 0.70,
        "Current liabilities dominated by bank borrowing.")

    # VI. CAPEX & DIVERSION
    if P:
        capex = L["bs_gross_block"] - P["bs_gross_block"]
        lt_funds = ((L["bs_tnw"] - P["bs_tnw"])
                    + (L["bs_ttl"] - P["bs_ttl"]))
        ratio = capex / max(0.01, lt_funds)
        add("RF18", "Capex & Diversion", "Capex funded from working capital",
            ratio if capex >= 0.5 else "no capex", "<= 1.0x",
            capex >= 0.5 and ratio > 1.0,
            "Gross block grew faster than long-term funds — WC likely diverted to capex (CRITICAL).")
        tnw_gap = (L["bs_tnw"] - P["bs_tnw"]) - L["os_retained"]
        add("RF19", "Capex & Diversion", "Capital withdrawal",
            round(tnw_gap, 2), ">= -1.0 Cr",
            tnw_gap < -1.0,
            "TNW change does not reconcile with retained profit — possible withdrawal.")
        usl_l = L["bs_unsecured_promoters"] + L["bs_unsecured_others"]
        usl_p = P["bs_unsecured_promoters"] + P["bs_unsecured_others"]
        usl_move = _pct(usl_l, usl_p) if usl_p >= 0.5 else None
        add("RF20", "Capex & Diversion", "Surge in unsecured loans",
            usl_move if usl_move is not None else "negligible USL", "Δ <= 100%",
            usl_move is not None and usl_move > 1.0,
            "Unsecured loans more than doubled — related-party movement check.",
            status_breach="VERIFY")
    else:
        for rid, name in (("RF18", "Capex funded from working capital"),
                          ("RF19", "Capital withdrawal"),
                          ("RF20", "Surge in unsecured loans")):
            flags.append(Flag(rid, "Capex & Diversion", name, None, "—",
                              "N/A", "Needs a prior audited year"))

    # VII. CONTINGENT EXPOSURES
    tnw = L["bs_tnw"] if L["bs_tnw"] > 0 else None
    for rid, num, cap, label in (
            ("RF21", L["bs_guarantees_memo"], 0.50, "Corporate guarantees > 50% of TNW"),
            ("RF22", L["bs_inv_group_cos"], 0.25, "Group investments > 25% of TNW"),
            ("RF23", L["bs_loans_group"] + L["bs_ar_group"]
                     + L["bs_receivables_directors"], 0.10,
             "Group/director loans > 10% of TNW")):
        share = (num / tnw) if tnw else None
        add(rid, "Contingent Exposure", label,
            share, f"<= {cap:.0%}",
            share is not None and share > cap,
            "Off-balance-sheet / group exposure concentration is significant.")

    # VIII. TANDON NORMS
    add("RF24", "Tandon Norms", "ABF > 75% of TCA (over-banked)",
        stbb_share, "<= 75%",
        stbb_share is not None and stbb_share > 0.75,
        "Assessed bank finance funds too much of current assets.")
    nwc_share = mL["nwc"] / mL["tca"] if mL["tca"] else None
    add("RF25", "Tandon Norms", "NWC < 25% of TCA (margin breach)",
        nwc_share, ">= 25%",
        nwc_share is not None and nwc_share < 0.25,
        "Borrower margin below the Tandon-II stipulation.")

    # IX. DIRECTIONAL TESTS
    recv_old_share = (L["bs_receivables_6m"] / L["bs_tca"]) if L["bs_tca"] else None
    add("RF26", "Directional", "Receivables aged > 6 months",
        recv_old_share, "low share of TCA",
        recv_old_share is not None and recv_old_share > 0.05,
        "Material stock of old receivables — collection weakness.")
    add("RF27", "Directional", "No tax provision in profitable year",
        L["bs_provision_tax"], "> 0 when PBT > 0",
        L["os_pbt"] > 1 and L["bs_provision_tax"] <= 0,
        "Profitable year but no tax provision — check MAT / b/f losses.",
        status_breach="VERIFY")
    cash_share = ((L["bs_cash"] + L["bs_fixed_deposits"]) / L["os_net_sales"]
                  if L["os_net_sales"] else None)
    add("RF28", "Directional", "Cash & equivalents < 1% of net sales",
        cash_share, ">= 1%",
        cash_share is not None and cash_share < 0.01,
        "Cash position unusually thin for the scale of operations.")

    # X. ASSET QUALITY
    nb_gb = (L["bs_net_block"] / L["bs_gross_block"]) if L["bs_gross_block"] else None
    add("RF29", "Asset Quality", "Net/Gross block < 30% (aged assets)",
        nb_gb, ">= 30%",
        nb_gb is not None and nb_gb < 0.30,
        "Asset base heavily depreciated — re-investment risk.")
    add("RF30", "Asset Quality", "FACR below threshold",
        mL["facr"], f">= {t['min_facr']}",
        L["bs_ttl"] > 0.5 and mL["facr"] is not None
        and mL["facr"] < t["min_facr"],
        "Fixed assets cover term debt too thinly.")

    # XI. REGULATORY OVERLAYS (project finance)
    meta = ctx["meta"]
    pf = meta.get("project_finance", "No").lower() == "yes"
    under_constr = meta.get("project_phase", "").lower() == "under-construction"
    add("RF31", "Regulatory", "Project finance provisioning overlay",
        meta.get("project_sector", "—") if pf else "not PF",
        "RBI PF Directions 2025",
        pf and under_constr,
        "Under-construction project finance — elevated standard-asset "
        "provisioning applies; price for capital consumption.",
        status_breach="VERIFY")
    add("RF32", "Regulatory", "DSCR suppression pre-DCCO",
        meta.get("dcco_year", "—") if pf else "not PF",
        "suppress DSCR alerts during construction",
        pf and under_constr,
        "Construction phase: DSCR covenant applies only from DCCO — "
        "interpret coverage breaches accordingly.",
        status_breach="VERIFY")

    return flags


def red_flag_verdict(flags):
    n = sum(1 for f in flags if f.triggered)
    if n == 0:
        return "NO MATERIAL RED FLAGS"
    if n <= 3:
        return "MINOR CONCERNS — ADDRESS IN COVENANTS"
    if n <= 7:
        return "MULTIPLE CONCERNS — DEEP DIVE NEEDED"
    return "MATERIAL CREDIT CONCERNS — RECONSIDER PROPOSAL"


# ── 12B_Exception_Engine: 13 deep-diagnostic tests ────────────────────────────

def run_exception_engine(ctx):
    L, P, mL = ctx["L"], ctx["P"], ctx["mL"]
    flags = []

    def add(rule_id, test, value, threshold, breach, detail):
        flags.append(Flag(
            rule_id, "Exception Engine", test,
            round(value, 4) if isinstance(value, float) else value,
            threshold, "BREACH" if breach else "OK",
            detail if breach else ""))

    if P is None:
        return [Flag("EX*", "Exception Engine", "All 13 tests", None, "—",
                     "N/A", "Needs a prior audited year")]

    recv_l = L["bs_receivables_domestic"] + L["bs_receivables_export"]
    recv_p = P["bs_receivables_domestic"] + P["bs_receivables_export"]
    sales_g = _pct(L["os_net_sales"], P["os_net_sales"]) or 0
    recv_g  = _pct(recv_l, recv_p)
    add("EX1", "Sudden receivable spike vs sales", recv_g,
        "recv Δ<=50% unless sales support",
        recv_g is not None and recv_g > 0.50 and sales_g < 0.20,
        "Receivables grew >50% on <20% sales growth — channel stuffing / "
        "collection failure pattern. Verify debtor ageing + GST register.")

    inv_g = _pct(L["bs_inventory_total"], P["bs_inventory_total"])
    add("EX2", "Inventory vs sales growth mismatch",
        inv_g, "inventory growth <= 2x sales growth",
        inv_g is not None and sales_g > 0 and inv_g > 2 * sales_g
        and inv_g > 0.10,
        "Inventory growing far faster than sales — slow stock or DP inflation. "
        "Inspect stock audit and movement registers.")

    add("EX3", "PAT positive but NWC severely negative",
        mL["nwc"], "NWC stable when PAT > 0",
        L["os_pat"] > 0 and mL["nwc"] < 0,
        "Profits are not cash-backed — reconcile PAT to operating cash flow.")

    ebitda_g = _pct(L["os_ebitda"], P["os_ebitda"])
    add("EX4", "EBITDA up while sales fall", ebitda_g,
        "not > +10% when sales decline",
        ebitda_g is not None and ebitda_g > 0.10 and sales_g < 0,
        "EBITDA improved on falling revenue — check one-off income or "
        "reclassification inflating EBITDA.")

    ttl_g = _pct(L["bs_ttl"], P["bs_ttl"]) if P["bs_ttl"] > 0.5 else None
    add("EX5", "Aggressive leverage jump", ttl_g,
        "TTL Δ <= 50% in one year",
        ttl_g is not None and ttl_g > 0.50,
        "Term debt grew >50% in a year — verify purpose and DSCR headroom.")

    stbb_tca = (L["bs_stbb_total"] / L["bs_tca"]) if L["bs_tca"] else None
    add("EX6", "STBB funding long-term assets", stbb_tca,
        "STBB <= 60% of TCA",
        stbb_tca is not None and stbb_tca > 0.60,
        "Short-term bank borrowing disproportionate to current assets — "
        "possible diversion to fixed assets. Fund-flow analysis needed.")

    old_share = (L["bs_receivables_6m"] / recv_l) if recv_l else None
    add("EX7", "Old receivables concentration", old_share,
        "<= 15% of total debtors",
        old_share is not None and old_share > 0.15,
        ">15% of debtors aged 6m+ — chronic collection weakness. "
        "Obtain ageing report; check related-party names.")

    usl_delta = ((L["bs_unsecured_promoters"] + L["bs_unsecured_others"])
                 - (P["bs_unsecured_promoters"] + P["bs_unsecured_others"]))
    usl_sales = usl_delta / L["os_net_sales"] if L["os_net_sales"] else None
    add("EX8", "Rapid unsecured loan movement", usl_sales,
        "Δ USL <= 10% of net sales",
        usl_sales is not None and usl_sales > 0.10,
        "USL rose by >10% of sales — related-party infusion/diversion check.")

    wc_assets_g = _pct(L["bs_inventory_total"] + recv_l,
                       P["bs_inventory_total"] + recv_p)
    add("EX9", "Revenue–working-asset disconnect", sales_g,
        "sales jump needs inventory/debtor support",
        sales_g > 0.30 and wc_assets_g is not None and wc_assets_g < 0.05,
        "Revenue jumped >30% with flat inventory+debtors — verify turnover "
        "via GSTR-3B / 26AS / bank credits.")

    audited = ctx["audited"]
    projected = ctx["projected"]
    if len(audited) >= 2 and projected:
        first_s, last_s = audited[0]["lines"]["os_net_sales"], L["os_net_sales"]
        proj_s = projected[-1]["lines"]["os_net_sales"]
        hist_cagr = ((last_s / first_s) ** (1 / (len(audited) - 1)) - 1
                     if first_s > 0 and last_s > 0 else None)
        proj_cagr = ((proj_s / last_s) ** (1 / len(projected)) - 1
                     if last_s > 0 and proj_s > 0 else None)
        add("EX10", "Projected CAGR vs historical", proj_cagr,
            "<= 2x historical CAGR",
            None not in (hist_cagr, proj_cagr) and hist_cagr > 0
            and proj_cagr > 2 * hist_cagr,
            "Projected growth more than doubles demonstrated history — "
            "challenge order book and capacity assumptions.")

    # STBB growing in step with sales is normal while TL amortises; the
    # diversion signature is STBB growth OUTPACING sales growth as TL falls.
    stbb_delta = L["bs_stbb_total"] - P["bs_stbb_total"]
    ttl_delta  = L["bs_ttl"] - P["bs_ttl"]
    stbb_g     = _pct(L["bs_stbb_total"], P["bs_stbb_total"])
    add("EX11", "STBB replacing term debt", round(stbb_delta, 2),
        "STBB rise not > TTL fall (beyond sales growth)",
        ttl_delta < -0.5 and stbb_delta > abs(ttl_delta)
        and stbb_g is not None and stbb_g > sales_g + 0.10,
        "Short-term lines appear to be rolling long-term debt — "
        "evergreening risk; review end-use of TL repayments.")

    inst = L["ds_tl_inst_applicant"] + L["ds_tl_inst_other"]
    int_ratio = L["os_interest_tl"] / inst if inst > 0 else None
    add("EX12", "Interest capitalisation risk", int_ratio,
        "TL interest / instalments <= 1.5x",
        int_ratio is not None and int_ratio > 1.5,
        "TL interest far exceeds principal repayment — verify amortisation "
        "schedule for evergreening.")

    usl_tnw = ((L["bs_unsecured_promoters"] + L["bs_unsecured_others"])
               / L["bs_tnw"]) if L["bs_tnw"] > 0 else None
    add("EX13", "Related-party USL dependency", usl_tnw,
        "USL / TNW <= 30%",
        usl_tnw is not None and usl_tnw > 0.30,
        "Unsecured loans exceed 30% of net worth — confirm subordination "
        "and restrict repayment without bank consent.")

    return flags


# ── 17_Advanced_EWS: RBI fraud-risk indicators ────────────────────────────────

# Qualitative catalogue (manual Yes/No). Severity per the workbook.
EWS_CATALOGUE = {
    1:  ("Default/delay in undisputed statutory payments (GST/PF/IT/ESI)", "Critical"),
    2:  ("Bouncing of high-value cheques / systematic payment failures",   "High"),
    3:  ("Frequent BG invocation / LC devolvement",                        "High"),
    4:  ("Continuous overdrawing of CC limit (>30 days in 6 months)",      "High"),
    5:  ("Frequent ad-hoc/temporary limit requests (>3 in 12 months)",     "Medium"),
    6:  ("Inventory movements not commensurate with sales",                "High"),
    7:  ("Funds from other banks used to service own-bank dues",           "High"),
    8:  ("Onerous open-ended clauses in BGs/LCs",                          "Medium"),
    9:  ("High-value inward returns (>5% of turnover)",                    "Medium"),
    10: ("Frequent advance/excess drawal requests",                        "Medium"),
    11: ("Persistent delay in stock statements / financial data",          "Medium"),
    12: ("Unexplained changes in fund utilisation / end-use",              "High"),
    13: ("Resistance / adverse remarks in concurrent or stock audits",     "High"),
    14: ("Frequent changes in management / Board",                         "Medium"),
    15: ("Resignation of CFO / Auditor / Independent Director",            "Medium"),
    16: ("Promoter pledge of shares (>50% of holding)",                    "High"),
    17: ("Promoter / family disputes affecting control",                   "Medium"),
    18: ("Concealment of material facts in CMA / application",             "Critical"),
    19: ("Forensic audit qualification / evidence of diversion",           "Critical"),
    20: ("Significant non-arm's-length related-party transactions",        "High"),
    21: ("Material weakness in internal controls per auditor",             "High"),
    22: ("Borrower mobile flagged on DoT fraud-risk indicator",            "Critical"),
    23: ("Title deed discrepancies / valuation overstatement",             "High"),
    24: ("Negative mainstream/business media coverage (6 months)",         "Medium"),
    25: ("Negative news about group companies",                            "Medium"),
    26: ("Industry-wide stress (sector NPA > 5%)",                         "Low"),
    27: ("Loss of major customer/contract (>20% of revenue)",              "High"),
    28: ("Reputational/quality issues with major customers",               "Medium"),
    29: ("Regulatory action against borrower (SEBI/MCA/GST/IT)",           "High"),
    30: ("High-value tax demands / litigation (>10% of TNW)",              "Medium"),
    31: ("Adverse environmental / social litigation",                      "Medium"),
    38: ("Auditor qualification or Emphasis of Matter",                    "High"),
    39: ("Audit fees declining sharply (>30% YoY)",                        "Medium"),
    40: ("Mid-year statutory auditor change without prescribed reasons",   "Medium"),
    41: ("Excessive promoter remuneration (>5% of revenue)",               "Medium"),
    42: ("Group exposure spike — cross-guarantees / advances >25% TNW",    "High"),
}

SEVERITY_POINTS = {"Critical": 10, "High": 5, "Medium": 2, "Low": 1}


def run_ews(ctx, manual_triggers=None, db_path=None):
    """
    Auto-detects the financial-behavior indicators (workbook #32–37) from
    CMA data and merges the qualitative ones passed as manual_triggers
    (iterable of catalogue ids). Returns (indicator dicts, rfa_required,
    rfa_reason).
    """
    manual = set(manual_triggers or [])
    L, P, mL, mP = ctx["L"], ctx["P"], ctx["mL"], ctx["mP"]
    indicators = []

    def auto(num, text, severity, triggered, value=None):
        indicators.append({
            "id": num, "indicator": text, "severity": severity,
            "triggered": bool(triggered), "source": "auto",
            "value": round(value, 4) if isinstance(value, float) else value,
        })

    if P is not None:
        opm_move = _pct(mL["opm"], mP["opm"])
        auto(32, "Operating margin decline > 10% YoY", "Medium",
             opm_move is not None and opm_move < -0.10, opm_move)

        audited = ctx["audited"]
        pat_series = [y["lines"]["os_pat"] for y in audited]
        declining = (len(pat_series) >= 3
                     and pat_series[-1] < pat_series[-2] < pat_series[-3])
        auto(33, "PAT declining for 2+ consecutive years", "Medium", declining)

        inv_move = _pct(mL["inventory_days"], mP["inventory_days"])
        auto(34, "Inventory days +30% YoY", "Medium",
             inv_move is not None and inv_move > 0.30, inv_move)
        dso_move = _pct(mL["dso"], mP["dso"])
        auto(35, "DSO +30% YoY", "Medium",
             dso_move is not None and dso_move > 0.30, dso_move)

        usl_l = L["bs_unsecured_promoters"] + L["bs_unsecured_others"]
        usl_p = P["bs_unsecured_promoters"] + P["bs_unsecured_others"]
        usl_move = _pct(usl_l, usl_p) if usl_p > 0.5 else None
        auto(36, "Unsecured loans +20% YoY", "Medium",
             usl_move is not None and usl_move > 0.20, usl_move)

        # CFO proxy: cash accruals less working-capital build
        cfo_proxy = L["os_cash_accruals"] - (mL["nwc"] - mP["nwc"])
        auto(37, "Operating cash flow (proxy) negative", "High",
             cfo_proxy < 0, cfo_proxy)

    # Statistical anomaly tie-in (workbook #44): any flagged anomaly engine
    bid = ctx["borrower"]["borrower_id"]
    with sqlite3.connect(db_path or DB) as conn:
        anom = conn.execute("""
            SELECT COUNT(*) FROM anomaly_result
            WHERE borrower_id = ? AND is_flagged = 1
        """, (bid,)).fetchone()[0]
    auto(44, "Statistical anomaly engine flagged", "High", anom > 0, anom)

    # Adverse media on record (workbook #24) — fed by data/news_intel.py
    try:
        from data.news_intel import count_adverse
        n_adverse = count_adverse(bid, db_path=db_path or DB)
        auto(24, "Negative news in mainstream/business media (12 months)",
             "Medium", n_adverse > 0, n_adverse)
    except Exception:
        pass

    # Adverse public-registry findings — fed by data/registry_checks.py
    try:
        from data.registry_checks import adverse_registries
        for registry, remarks, ews_id in adverse_registries(
                bid, db_path=db_path or DB):
            text, severity = EWS_CATALOGUE.get(
                ews_id, (f"Adverse registry finding ({registry})", "High"))
            indicators.append({
                "id": ews_id,
                "indicator": f"{text} [{registry}: {remarks or 'adverse'}]",
                "severity": severity, "triggered": True,
                "source": f"registry:{registry}", "value": None,
            })
    except Exception:
        pass

    for num in sorted(manual):
        if num in EWS_CATALOGUE:
            text, severity = EWS_CATALOGUE[num]
            indicators.append({
                "id": num, "indicator": text, "severity": severity,
                "triggered": True, "source": "manual", "value": None,
            })

    triggered = [i for i in indicators if i["triggered"]]
    critical  = [i for i in triggered if i["severity"] == "Critical"]
    high      = [i for i in triggered if i["severity"] == "High"]

    rfa = bool(critical) or len(high) >= 2
    reason = ""
    if critical:
        reason = (f"Critical EWS triggered: "
                  f"{'; '.join(i['indicator'] for i in critical)}")
    elif rfa:
        reason = f"{len(high)} High-severity EWS triggered (threshold: 2)"

    return indicators, rfa, reason


# ── Aggregate entry point ─────────────────────────────────────────────────────

def run_full_ews(borrower_name=None, manual_triggers=None, db_path=None):
    """Red flags + exception engine + RBI EWS in one report."""
    db = db_path or DB
    ctx = _context(borrower_name, db)
    if ctx is None:
        return EWSReport(borrower_name=borrower_name or "NOT FOUND",
                         error="No CMA data ingested for this borrower")

    report = EWSReport(borrower_name=ctx["borrower"]["name"],
                       basis=ctx["basis"])
    report.red_flags  = run_red_flags(ctx)
    report.exceptions = run_exception_engine(ctx)
    report.red_flag_verdict = red_flag_verdict(report.red_flags)
    report.ews_indicators, report.rfa_required, report.rfa_reason = run_ews(
        ctx, manual_triggers=manual_triggers, db_path=db)
    return report
