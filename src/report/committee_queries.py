"""
Committee query generator — turns every adverse finding into the pointed
questions a sanctioning authority should put to the analyst presenting
the proposal.

Fully deterministic: each query is template-driven with the actual numbers
interpolated, so nothing can be asked that the data doesn't support.

Sources scanned:
  * threshold breaches (current ratio, DSCR, TOL/TNW, Debt/EBITDA, ICR, FACR)
  * triggered red flags and exception-engine tests (by rule id)
  * RBI EWS indicators and the RFA condition
  * projection scrutiny verdicts (AGGRESSIVE / OPTIMISTIC) and CAGR gaps
  * the dual-column estimate-vs-audited variance (projection credibility)
  * proposed working-capital limit vs assessed MPBF
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


def _q(priority, parameter, observation, question, rule_id=None):
    return {"priority": priority, "parameter": parameter,
            "observation": observation, "question": question,
            "rule_id": rule_id, "manual_ref": None}


# Search phrase + a gate word the passage must contain, per rule/parameter.
# Citations are retrieved from the indexed loan manual (deterministic FTS,
# no LLM) and attached beneath the question in the memo.
MANUAL_GROUNDING = {
    "RF3":  ("verification of sales turnover sales register", "sales"),
    "RF5":  ("benchmark current ratio working capital assessment", "current ratio"),
    "RF6":  ("net working capital long term sources margin", "working capital"),
    "RF7":  ("net working capital long term sources margin", "working capital"),
    "RF8":  ("TOL TNW gearing benchmark net worth", "tnw"),
    "RF11": ("interest coverage ratio benchmark", "interest"),
    "RF12": ("debt service coverage ratio benchmark term loan", "coverage"),
    "RF13": ("debt service coverage ratio benchmark term loan", "coverage"),
    "RF14": ("inventory holding level norms assessment", "inventory"),
    "RF15": ("receivables holding period debtors norms", "receivab"),
    "RF18": ("diversion of funds end use monitoring", "diversion"),
    "RF19": ("withdrawal of funds capital net worth", "withdraw"),
    "RF20": ("unsecured loans quasi equity subordination promoters", "unsecured"),
    "RF21": ("corporate guarantee exposure norms", "guarantee"),
    "RF22": ("exposure group companies investments", "group"),
    "RF23": ("advances to group companies directors", "group"),
    "RF24": ("margin bank finance current assets second method", "margin"),
    "RF25": ("second method of lending margin current assets", "margin"),
    "RF26": ("receivables older than six months drawing power", "receivab"),
    "RF30": ("security margin asset coverage term loan", "security"),
    "EX1":  ("verification debtors receivables ageing", "receivab"),
    "EX8":  ("unsecured loans quasi equity subordination", "unsecured"),
    "EX10": ("projections assumptions validation acceptance", "project"),
    "EX11": ("rollover evergreening repayment term loan", "term loan"),
    "EX13": ("unsecured loans quasi equity subordination", "unsecured"),
    "Limit assessment":   ("maximum permissible bank finance assessment "
                           "working capital method", "bank finance"),
    "New unit":           ("appraisal of new projects promoter contribution "
                           "project report", "project"),
    "Estimate credibility": ("validation of estimates projections "
                             "reasonableness", "projection"),
    "Due diligence":      ("pre-sanction survey due diligence opinion "
                           "reports", "due diligence"),
    "RFA / fraud risk":   ("early warning signals red flagged account "
                           "fraud", "fraud"),
}


def _attach_manual_refs(queries, db_path):
    """Cite the manual under each query where a grounded passage exists."""
    try:
        from data.loan_manual import search_manual, manual_indexed
        if manual_indexed(db_path=db_path) == 0:
            return
    except Exception:
        return
    cache = {}
    for q in queries:
        key = q.get("rule_id") or q["parameter"]
        if key not in MANUAL_GROUNDING:
            key = "EX10" if q["parameter"].startswith("Projection") else None
        if key is None:
            continue
        if key not in cache:
            phrase, gate = MANUAL_GROUNDING[key]
            hits = search_manual(phrase, k=2, db_path=db_path)
            ref = None
            for h in hits:
                if gate.lower() in h["content"].lower():
                    extract = h["content"][:240]
                    extract = extract[:extract.rfind(" ")] + " …"
                    ref = {"chapter": f"{h['part']} {h['chapter']}".strip(),
                           "extract": extract}
                    break
            cache[key] = ref
        q["manual_ref"] = cache[key]


def _fmt(v, pat="{:,.2f}"):
    return "—" if v is None else (pat.format(v) if isinstance(v, (int, float)) else str(v))


# ── Red flag / exception rule → question templates ───────────────────────────
# {v} = flag value. Priority: 1 = put to the analyst first.

RULE_QUESTIONS = {
    "RF1":  (2, "Profitability", "PAT of {v:.2f} Cr in the latest audited year.",
             "Is the loss one-off or structural? Provide the turnaround plan "
             "with quarterly milestones, and promoter support letter if relied upon."),
    "RF3":  (2, "Sales authenticity", "Sales grew {v:.0%} year on year.",
             "Has turnover been cross-verified with GSTR-1/3B and Form 26AS? "
             "Table the reconciliation and the top-5 customer concentration."),
    "RF4":  (3, "Sales decline", "Sales growth of {v:.0%} breached the acceptable floor.",
             "What caused the decline, and why do the projections assume reversal? "
             "Is the working capital limit sized on declining or projected turnover?"),
    "RF5":  (2, "Liquidity", "Current ratio at {v:.2f} against the sanction benchmark.",
             "What corrects the liquidity position, and by when? Confirm no current "
             "liabilities are being rolled over to dress the ratio at year-end."),
    "RF6":  (1, "Liquidity", "Net working capital is negative ({v:.2f} Cr).",
             "Short-term funds are financing long-term uses. What long-term "
             "infusion (equity / subordinated USL) restores positive NWC?"),
    "RF7":  (2, "NWC erosion", "NWC eroded {v:.0%} versus the prior year.",
             "Where did the working capital go — losses, capex, or withdrawals? "
             "Provide the funds-flow statement for the year."),
    "RF8":  (2, "Leverage", "TOL/TNW at {v:.2f} exceeds the cap.",
             "What equity or subordinated infusion is committed, with dates? "
             "Are unsecured loans documented as quasi-equity with non-withdrawal undertakings?"),
    "RF9":  (1, "Solvency", "Net worth is negative or nil ({v:.2f} Cr).",
             "Why is fresh exposure proposed to a borrower with no equity cushion? "
             "What is the recapitalisation plan and promoter contribution?"),
    "RF10": (2, "Leverage", "Term debt at {v:.1f}x EBITDA.",
             "How does the borrower deleverage to the policy multiple? Stress the "
             "repayment schedule at EBITDA 20% lower and present the result."),
    "RF11": (2, "Coverage", "Interest cover at {v:.2f}.",
             "At this coverage, what absorbs a rate reset or a margin dip? "
             "Has interest been serviced on time through the last 12 months?"),
    "RF12": (1, "Debt service", "Gross DSCR falls to {v:.2f}x in at least one year.",
             "Identify the breach year and the source of debt service that year. "
             "Why is restructuring of the repayment schedule not warranted?"),
    "RF13": (2, "Debt service", "Average gross DSCR at {v:.2f}x is below benchmark.",
             "Justify sanction below the DSCR floor: what structural mitigants — "
             "DSRA, escrow, sponsor undertaking — are built into the terms?"),
    "RF15": (3, "Receivables", "DSO stretched {v:.0%} year on year.",
             "Table the debtor ageing. How much is over 90/180 days, who are the "
             "delinquent counterparties, and are any related parties?"),
    "RF16": (3, "Payables", "Creditor days stretched {v:.0%}.",
             "Is the borrower financing itself by delaying suppliers? Confirm no "
             "devolved LCs or supplier legal notices are outstanding."),
    "RF18": (1, "Diversion", "Capex of recent years exceeds long-term funds raised (ratio {v}).",
             "Working capital appears to have funded fixed assets. Produce the "
             "end-use certificates and the fund-flow for the capex period."),
    "RF19": (1, "Capital withdrawal", "TNW moved {v:.2f} Cr against retained profit.",
             "Reconcile net worth movement with retained earnings. Who withdrew "
             "capital, under what authorisation, while bank debt was outstanding?"),
    "RF20": (2, "Unsecured loans", "Unsecured loans moved {v:.0%}.",
             "Provide the lender-wise USL schedule. Are inflows from group entities, "
             "and were any USL repayments made out of bank limits?"),
    "RF21": (2, "Off-balance-sheet", "Corporate guarantees at {v:.0%} of TNW.",
             "List each guaranteed entity with its financial position. What is the "
             "probability-weighted devolvement impact on TNW?"),
    "RF22": (2, "Group exposure", "Group investments at {v:.0%} of TNW.",
             "Why is the borrower's capital parked in group companies while it "
             "borrows? Are these investments encumbered or impaired?"),
    "RF23": (2, "Group exposure", "Loans to group/directors at {v:.0%} of TNW.",
             "Justify lending to the borrower while it on-lends to the group. "
             "Demand repayment or set-off before/at disbursement."),
    "RF24": (2, "Over-banking", "Bank finance funds {v:.0%} of current assets.",
             "Why does bank money carry this share of the working capital cycle? "
             "What is the borrower's own margin contribution?"),
    "RF25": (2, "Tandon margin", "NWC at {v:.0%} of current assets (norm: 25%).",
             "The borrower's margin is below the Tandon-II stipulation. What is the "
             "margin build-up plan, or is the limit to be conditioned on it?"),
    "RF26": (3, "Stale receivables", "Receivables older than 6 months at {v:.0%} of TCA.",
             "Are these collectible or effectively bad? Why are they not excluded "
             "from drawing power, and is provisioning adequate?"),
    "RF27": (3, "Tax anomaly", "No tax provision despite profit ({v}).",
             "Explain the nil provision — MAT, brought-forward losses, or "
             "under-provisioning that overstates PAT?"),
    "RF28": (3, "Cash position", "Cash at {v:.1%} of net sales.",
             "How does the unit meet payroll and statutory dues at this cash level? "
             "Verify no statutory arrears (GST/PF/TDS) exist."),
    "RF29": (3, "Asset age", "Net block is {v:.0%} of gross block.",
             "The asset base is heavily depreciated. What replacement capex is "
             "needed to sustain projected output, and how is it funded?"),
    "RF30": (2, "Security cover", "FACR at {v:.2f}.",
             "Asset cover for term debt is below threshold. What collateral tops "
             "up the shortfall, and at what valuation date?"),
    "EX1":  (1, "Receivable spike", "Receivables grew {v:.0%} while sales lagged.",
             "Classic channel-stuffing signature. Produce the debtor ageing and "
             "match the top debtors against GSTR-1 sales entries."),
    "EX2":  (2, "Inventory build", "Inventory grew {v:.0%}, far ahead of sales.",
             "Is stock slow-moving, obsolete, or inflated for drawing power? When "
             "was the last stock audit, and what did it find?"),
    "EX3":  (1, "Earnings quality", "PAT positive but NWC severely negative ({v:.2f} Cr).",
             "Profits are not converting to cash. Present the PAT-to-cash-flow "
             "bridge and debtor/creditor reconciliation."),
    "EX4":  (3, "EBITDA quality", "EBITDA up {v:.0%} on falling sales.",
             "Decompose the margin gain: cost reduction, one-offs, or accounting "
             "reclassification? Is it sustainable next year?"),
    "EX5":  (2, "Leverage jump", "Term debt up {v:.0%} in a single year.",
             "What was the new debt for, who sanctioned it, and were we informed? "
             "Re-run DSCR including the full new obligations."),
    "EX6":  (1, "Maturity mismatch", "Short-term bank borrowing at {v:.0%} of current assets.",
             "Are working capital limits funding fixed assets or losses? Produce "
             "the utilisation analysis and end-use certification."),
    "EX7":  (2, "Old debtors", "{v:.0%} of receivables are older than 6 months.",
             "Why do these remain in current assets and drawing power? Identify "
             "related-party balances within them."),
    "EX8":  (2, "USL movement", "Unsecured loans moved by {v:.0%} of net sales.",
             "Trace the USL counterparties. Is this round-tripping or genuine "
             "promoter support? Confirm subordination in writing."),
    "EX9":  (1, "Revenue authenticity", "Sales jumped while inventory+debtors stayed flat.",
             "This pattern is inconsistent for a manufacturer. Reconcile reported "
             "sales with GSTR-3B, 26AS TDS credits and bank credits."),
    "EX10": (1, "Projection realism", "Projected CAGR {v:.0%} is more than double history.",
             "What contracted order book, capacity addition or new customer "
             "justifies this? Re-present MPBF and DSCR at historical growth."),
    "EX11": (2, "Evergreening", "Short-term borrowings rose {v:.2f} Cr as term debt fell.",
             "Are WC lines repaying term obligations? Trace TL repayment sources "
             "through the account statements."),
    "EX12": (2, "Repayment structure", "TL interest at {v:.2f}x of principal repaid.",
             "The loan is interest-heavy — is principal actually amortising, or "
             "being refinanced? Table the amortisation schedule against payments."),
    "EX13": (2, "USL dependency", "Unsecured loans at {v:.0%} of net worth.",
             "These funds can exit overnight. Obtain subordination plus a "
             "non-withdrawal undertaking as a sanction condition."),
}

EWS_QUESTIONS = {
    32: "Operating margin compressed >10% — what changed in the cost structure or pricing power?",
    33: "PAT has declined for consecutive years — is this a structural decline the projections ignore?",
    34: "Inventory days jumped >30% — when was stock last physically audited?",
    35: "DSO jumped >30% — present collections efficiency and debtor ageing.",
    36: "Unsecured loans rose >20% — who injected funds, and why mid-cycle?",
    37: "Operating cash flow (proxy) is negative — how is the unit funding itself day to day?",
    44: "The statistical anomaly engine flagged this account — which engine, and what did "
        "the forensic review of the trigger find?",
}


def generate_queries(borrower_name=None, db_path=None):
    """All committee queries for a borrower, ordered by priority."""
    from analytics.wc_assessment import assess_working_capital
    from analytics.ews import run_full_ews
    from analytics.forecast import scrutinize_projections

    db = db_path or DB
    a = assess_working_capital(borrower_name, db_path=db)
    if a.error:
        return []
    queries = []

    # 1 ── Projection credibility: the dual column
    audited, basis_label = a.assessment_basis
    if audited and basis_label == "audited":
        dual = next((y for y in a.years if y["is_dual"]
                     and y["fy_label"] == audited["fy_label"]), None)
        if dual:
            est, act = dual["metrics"]["net_sales"], audited["metrics"]["net_sales"]
            if est and act and abs(act - est) / abs(est) > 0.10:
                var = (act - est) / est
                queries.append(_q(
                    1, "Estimate credibility",
                    f"Last year's estimate for FY {audited['fy_label']} sales was "
                    f"{_fmt(est)} Cr; audited actuals came in at {_fmt(act)} Cr "
                    f"({var:+.0%}).",
                    "Given this miss, why should the current estimates and "
                    "projections be accepted at face value? Present the assessment "
                    "re-worked with a haircut equal to last year's variance."))

    # 2 ── Proposed limit vs assessed MPBF
    fb_wc = a.proposals.get("fb_wc", {})
    proposed = fb_wc.get("proposed")
    if audited and proposed:
        mpbf = audited["metrics"]["mpbf_method2"]
        if mpbf is not None and proposed > mpbf + 0.005:
            queries.append(_q(
                1, "Limit assessment",
                f"Proposed FB-WC limit is {_fmt(proposed)} Cr against assessed "
                f"MPBF (Method II) of {_fmt(mpbf)} Cr on the "
                f"{basis_label.split(' — ')[0]} year {audited['fy_label']}.",
                "Justify the excess over MPBF: is it assessed on projected "
                "financials, and if so, why are those projections reliable "
                "(see scrutiny findings)? Otherwise restrict the limit."))
    if basis_label != "audited" and audited:
        queries.append(_q(
            1, "New unit",
            f"No audited financials exist; the entire assessment rests on "
            f"the borrower's own {basis_label.split(' — ')[0].lower()} "
            f"figures for {audited['fy_label']}.",
            "What independent evidence supports these projections — project "
            "report appraisal, supplier/customer contracts, promoter "
            "experience in this line, margin benchmarks? What is the DCCO, "
            "the implementation status, and the promoter contribution "
            "already brought in?"))

    # 3 ── Red flags and exception tests
    ews = run_full_ews(borrower_name, db_path=db)
    if not ews.error:
        if ews.rfa_required:
            queries.append(_q(
                1, "RFA / fraud risk",
                f"EWS aggregation meets the Red-Flagged-Account condition: "
                f"{ews.rfa_reason}.",
                "Has the account been flagged as RFA per the RBI Master Direction "
                "on Fraud Risk, and the prescribed review convened? If not, why not?"))
        for f in ews.triggered_red_flags + ews.triggered_exceptions:
            spec = RULE_QUESTIONS.get(f.rule_id)
            if not spec:
                continue
            priority, parameter, obs_tpl, question = spec
            try:
                observation = obs_tpl.format(v=f.value)
            except (ValueError, TypeError):
                import re
                observation = re.sub(r"\{v[^}]*\}", str(f.value), obs_tpl)
            queries.append(_q(priority, parameter, observation, question,
                              rule_id=f.rule_id))
        for i in ews.ews_indicators:
            if i["triggered"] and i["id"] in EWS_QUESTIONS:
                queries.append(_q(
                    2, f"EWS #{i['id']}", i["indicator"] + ".",
                    EWS_QUESTIONS[i["id"]]))

    # 3b ── Due-diligence gaps: registries unchecked or stale
    try:
        from data.registry_checks import due_diligence_gaps
        gaps = due_diligence_gaps(a.borrower_name, db_path=db)
        if gaps:
            labels = ", ".join(g["label"] for g in gaps)
            queries.append(_q(
                2, "Due diligence",
                f"Public-registry checks pending or older than 90 days: "
                f"{labels}.",
                "Confirm these searches were performed before placing the "
                "proposal, and table the findings: MCA charges (who else has "
                "lent?), GST filing regularity, IBBI/NCLT proceedings, EPFO "
                "defaults and pending litigation."))
    except Exception:
        pass

    # 4 ── Optimistic projections
    scr = scrutinize_projections(borrower_name, db_path=db)
    if not scr.get("error"):
        for metric, entry in scr["metrics"].items():
            if entry.get("error"):
                continue
            hot = [p for p in entry["projections"]
                   if p["verdict"] in ("AGGRESSIVE", "OPTIMISTIC")]
            if not hot:
                continue
            worst = hot[-1]
            queries.append(_q(
                2 if worst["verdict"] == "AGGRESSIVE" else 3,
                f"Projection — {metric}",
                f"Projected {metric} of {_fmt(worst['value'])} for "
                f"{worst['fy_label']} sits {'above the 95%' if worst['verdict'] == 'AGGRESSIVE' else 'above the 80%'} "
                f"statistical band (ETS mid-point {_fmt(worst['ets_point'])}).",
                f"What specific, evidenced driver lifts {metric} beyond the "
                f"trend — orders in hand, capacity commissioned, price escalation? "
                f"Re-present DSCR and MPBF at the ETS mid-point as a sensitivity."))

    _attach_manual_refs(queries, db)
    queries.sort(key=lambda q: q["priority"])
    return queries
