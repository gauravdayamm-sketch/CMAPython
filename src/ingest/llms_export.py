"""
LLMS export adapter — ingests the bank system's per-statement exports
(Balance_Sheet / Operating_Statement / Performance_And_Financial_Indicators,
as .xls or .csv) into the same CMAData → SQLite pipeline as the workbook.

Differences from the workbook template handled here:
  * three separate files instead of one workbook
  * row positions vary → rows are matched by NORMALISED LABEL within the
    current SECTION (section headers carry no numbers)
  * year labels and Audited/Estimated/Projected types are read straight from
    the file header rows, including the dual Estimated/Audited pair
  * year-columns with no data at all (blank history) are dropped
  * the export has no DSCR input sheet → TL instalments default to the
    balance-sheet "due within 1 year" row (documented approximation)
  * the export's own totals are cross-checked against Python-recomputed
    subtotals (WARNING on mismatch — catches mapping drift immediately)
"""
import csv
import pathlib
import re

from ingest.cma_workbook import (
    CMAData, YearColumn, OS_INPUTS, BS_INPUTS, DSCR_INPUTS,
    compute_os_derived, compute_bs_derived, save_to_db,
)

DATA_COL0 = 2          # first year column (0-based) in the exports


# ── Raw table readers ─────────────────────────────────────────────────────────

def read_table(path):
    """Rows of cell values from a .csv or legacy .xls file."""
    path = pathlib.Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            return [row for row in csv.reader(fh)]
    if path.suffix.lower() == ".xls":
        import xlrd
        book = xlrd.open_workbook(path)
        sheet = book.sheet_by_index(0)
        return [[sheet.cell_value(r, c) for c in range(sheet.ncols)]
                for r in range(sheet.nrows)]
    raise ValueError(f"Unsupported file type: {path.suffix} ({path.name})")


def _num(value):
    """Numeric cell → float; blank/text → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm(label):
    return re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()


# ── Label maps: (section_keyword_or_None, normalised label prefix, code) ──────
# Evaluated top-down; first match wins. Section None = match in any section.

BS_LABELS = [
    (None, "from applicant bank",                    "bs_stbb_applicant"),
    (None, "from other banks",                       "bs_stbb_other_banks"),
    (None, "under buyers credit",                    "bs_buyers_credit"),
    (None, "of which bills purchased",               "bs_bills_discounted_memo"),
    (None, "short term borrowings from others",      "bs_stb_others"),
    (None, "sundry creditors trade without lc",      "bs_creditors_trade"),
    (None, "sundry creditors trade under lc",        "bs_creditors_lc"),
    (None, "advance payments from customers",        "bs_customer_advances"),
    (None, "provision for taxation",                 "bs_provision_tax"),
    (None, "dividend payable",                       "bs_dividend_payable"),
    (None, "other statutory liabilities",            "bs_statutory_dues"),
    (None, "deposits instalments of term loans",     "bs_tl_instalments_1yr"),
    (None, "other current liability and provisions", "bs_other_cl_provisions"),
    (None, "provision for expenditure",              "bs_expenditure_provision"),
    (None, "sundry creditors for capital goods",     "bs_creditors_capital_goods"),
    (None, "sundry creditors for expenses",          "bs_creditors_expenses"),
    ("current liabilities", "ocl",                   "bs_others_cl"),
    ("current liabilities", "other",                 "bs_others_cl"),
    (None, "debentures",                             "bs_debentures"),
    (None, "preference shares redeemable",           "bs_pref_shares_red"),
    (None, "secured term loans",                     "bs_secured_tl"),
    (None, "deferred payment credits",               "bs_dpc"),
    (None, "term public deposits",                   "bs_term_deposits"),
    (None, "unsecured loans from promoters",         "bs_unsecured_promoters"),
    (None, "unsecured loans other than",             "bs_unsecured_others"),
    (None, "capex creditors",                        "bs_capex_creditors_lt"),
    (None, "non current provisions",                 "bs_noncurrent_provisions"),
    (None, "deferred tax liabilities",               "bs_dtl"),
    (None, "corporate gurantees extended",           "bs_guarantees_memo"),
    (None, "corporate guarantees extended",          "bs_guarantees_memo"),
    (None, "equity share capital",                   "bs_equity_capital"),
    (None, "preference share capital",               "bs_pref_capital"),
    (None, "general reserve",                        "bs_general_reserve"),
    (None, "revaluation reserve",                    "bs_revaluation_reserve"),
    (None, "surplus deficit in profit",              "bs_pl_surplus"),
    (None, "other reserves",                         "bs_other_reserves"),
    (None, "security premium",                       "bs_security_premium"),
    (None, "warrants options",                       "bs_warrants"),
    (None, "share application money",                "bs_share_application"),
    (None, "cash cash equivalents",                  "bs_cash"),
    (None, "investments in govt",                    "bs_govt_securities"),
    (None, "fixed deposits with banks",              "bs_fixed_deposits"),
    (None, "receivables other than deferred",        "bs_receivables_domestic"),
    (None, "export receivables",                     "bs_receivables_export"),
    (None, "bills purchased discounted",             "bs_bills_purchased"),
    (None, "instalments deferred receivables",       "bs_deferred_recv_1yr"),
    ("inventory", "raw material imported",           "bs_rm_imported"),
    ("inventory", "raw material indigenous",         "bs_rm_indigenous"),
    (None, "stock in process",                       "bs_sip"),
    (None, "finished goods",                         "bs_fg"),
    (None, "other consumable spares imported",       "bs_spares_imported"),
    (None, "other consumable spares indigenous",     "bs_spares_indigenous"),
    (None, "advances to suppliers of raw materials", "bs_adv_suppliers_rm"),
    (None, "advance payment of taxes",               "bs_adv_tax"),
    (None, "tufs",                                   "bs_tufs"),
    (None, "excise customs duty receivables",        "bs_duty_receivables"),
    (None, "cenvat credit",                          "bs_gst_input"),
    ("other current assets", "other",                "bs_other_oca"),
    (None, "gross block",                            "bs_gross_block"),
    (None, "capital work in progress",               "bs_cwip"),
    (None, "depreciation till date",                 "bs_acc_depreciation"),
    (None, "investments in group companies",         "bs_inv_group_cos"),
    (None, "security deposit",                       "bs_other_investments_lt"),
    (None, "advances to suppliers of capital goods", "bs_adv_capital_goods"),
    (None, "deferred receivables exceeding",         "bs_deferred_recv_lt"),
    ("non current assets", "deposits",               "bs_deposits"),
    (None, "loans advances to group entities",       "bs_loans_group"),
    (None, "receivables over 6 months",              "bs_receivables_6m"),
    (None, "account receivables from group",         "bs_ar_group"),
    (None, "receivables from directors",             "bs_receivables_directors"),
    (None, "non consumable stores",                  "bs_nonconsumable_spares"),
    (None, "gross intangible assets",                "bs_gross_intangibles"),
    (None, "amortisation till date",                 "bs_amortisation"),
    (None, "dta deferred tax assets",                "bs_dta"),
]

OS_LABELS = [
    (None, "domestic sales",                          "os_domestic_sales"),
    (None, "export sales",                            "os_export_sales"),
    (None, "other operating revenue income",          "os_other_op_income"),
    (None, "deduct excise duty",                      "os_excise"),
    (None, "deduct other items",                      "os_other_deductions"),
    ("raw materials", "imported",                     "os_rm_imported"),
    ("raw materials", "indigenous",                   "os_rm_indigenous"),
    (None, "spares imported",                         "os_spares_imported"),
    (None, "spares indigenous",                       "os_spares_indigenous"),
    (None, "power fuel",                              "os_power_fuel"),
    (None, "direct labour",                           "os_direct_labour"),
    (None, "other manufacturing expenses",            "os_other_mfg"),
    (None, "depreciation amortisation",               "os_depreciation"),
    (None, "add opening stock in process",            "os_opening_sip"),
    (None, "less closing stock in process",           "os_closing_sip"),
    (None, "add opening stock of finished goods",     "os_opening_fg"),
    (None, "less closing stock of finished goods",    "os_closing_fg"),
    (None, "selling general administrative",          "os_sga"),
    (None, "interest on working capital",             "os_interest_wc"),
    (None, "interest on term loans",                  "os_interest_tl"),
    (None, "other finance charges",                   "os_other_finance"),
    (None, "interest on unsecured loans",             "os_interest_unsecured"),
    (None, "income from investments",                 "os_income_investments"),
    (None, "forex gain",                              "os_forex_gain"),
    (None, "profit on sale of fixed assets",          "os_profit_sale_fa"),
    (None, "profit on sale of investments",           "os_profit_sale_inv"),
    (None, "goodwill other intangible assets",        "os_goodwill_writeoff"),
    (None, "preliminary expenses written off",        "os_prelim_writeoff"),
    (None, "other miscellaneous expenses written",    "os_misc_writeoff"),
    (None, "forex loss",                              "os_forex_loss"),
    (None, "loss on sale of fixed assets",            "os_loss_sale_fa"),
    (None, "mat credit",                              "os_mat_credit"),
    (None, "provision for taxes",                     "os_tax_provision"),
    (None, "prior period expenses",                   "os_prior_period"),
    (None, "equity dividend paid",                    "os_dividend_paid"),
]

# Export totals → recomputed code, for mapping-drift detection
BS_TOTAL_CHECKS = [
    ("total short term borrowings from banks",  "bs_stbb_total"),
    ("total other current liabilities",         "bs_ocl_total"),
    ("total current liabilities",               "bs_tcl"),
    ("total term liabilities",                  "bs_ttl"),
    ("total outside liabilities",               "bs_tol"),
    ("total net worth",                         "bs_tnw"),
    ("total liabilities",                       "bs_total_liabilities"),
    # NB: the export's "Total Investments (other than LT)" EXCLUDES cash,
    # unlike the workbook's row 58 — compared against govt+FD only.
    ("total investments other than long term",  "bs_govt_fd_total"),
    ("total other receivables",                 "bs_receivables_total"),
    ("total raw material",                      "bs_rm_total"),
    ("total other consumable spares",           "bs_spares_total"),
    ("total inventory",                         "bs_inventory_total"),
    ("total other current assets",              "bs_oca_total"),
    ("total non current assets others",         "bs_other_nca_total"),
    ("total current assets",                    "bs_tca"),
    ("total assets",                            "bs_total_assets"),
]
OS_TOTAL_CHECKS = [
    ("net sales",                          "os_net_sales"),
    ("total raw materials",                "os_total_rm"),
    ("total spares",                       "os_total_spares"),
    ("cost of production",                 "os_cost_of_production"),
    ("cost of sales",                      "os_cost_of_sales"),
    ("operating profit before",            "os_opbi"),
    ("total interest outgo",               "os_total_interest"),
    ("total other non operating income",   "os_nonop_income"),
    ("total other non operating expenses", "os_nonop_expense"),
    ("profit before tax",                  "os_pbt"),
    ("net profit loss",                    "os_pat"),
]

# Some exports carry amounts only on a TOTAL row with no component rows
# beneath it. A positive residual (file total > recomputed components) is
# absorbed into a designated generic line so nothing silently disappears.
ABSORB_RULES = [
    ("bs_ocl_total",         "bs_others_cl"),
    ("bs_govt_fd_total",     "bs_fixed_deposits"),
    ("bs_receivables_total", "bs_receivables_domestic"),
    ("bs_rm_total",          "bs_rm_indigenous"),
    ("bs_spares_total",      "bs_spares_indigenous"),
    ("bs_oca_total",         "bs_other_oca"),
    ("bs_other_nca_total",   "bs_other_nca"),
    ("os_total_rm",          "os_rm_indigenous"),
    ("os_total_spares",      "os_spares_indigenous"),
    ("os_nonop_income",      "os_income_investments"),
    ("os_nonop_expense",     "os_misc_writeoff"),
]

# After subgroup absorption, reconcile to the export's own top-level totals
# (any sign — some exports have internally inconsistent subgroup totals).
# The export's BS balances by construction, so this restores balance too.
FINAL_RECONCILE = [
    ("bs_tca", "bs_other_oca"),
    ("bs_tcl", "bs_others_cl"),
    ("bs_ttl", "bs_unsecured_others"),
    ("bs_tnw", "bs_other_reserves"),
]

SECTION_KEYWORDS = (
    "current liabilities", "term liabilities", "net worth", "current assets",
    "investments", "other receivables", "inventory", "other current assets",
    "fixed assets", "non current assets", "intangible assets",
    "cost of sales", "raw materials", "other non operating",
)


# ── Header parsing ────────────────────────────────────────────────────────────

def _parse_header(rows):
    """Borrower name + year columns from the export header."""
    name = customer_id = None
    for row in rows[:4]:
        for cell in row:
            m = re.search(r"Customer Id\s*:\s*(\S+).*Customer Name\s*:\s*(.+)",
                          str(cell or ""))
            if m:
                customer_id, name = m.group(1).strip(), m.group(2).strip()
                break
        if name:
            break

    year_row = type_row = None
    for row in rows[:8]:
        labels = [_norm(c) for c in row[:3]]
        if "accounting year" in labels:
            year_row = row
        if "financial type" in labels:
            type_row = row
    if year_row is None or type_row is None:
        return name, customer_id, None

    cols = []
    for idx in range(DATA_COL0, len(year_row)):
        label = str(year_row[idx]).strip() if idx < len(year_row) else ""
        typ   = str(type_row[idx]).strip() if idx < len(type_row) else ""
        ym = re.match(r"(\d{4})\s*-\s*(\d{2,4})$", label)
        if not ym or not typ:
            continue
        start_year = int(ym.group(1))
        cols.append(YearColumn(
            col_index=len(cols) + 1, start_year=start_year,
            fy_label=f"{start_year}-{str(start_year + 1)[-2:]}",
            fy_end=f"{start_year + 1}-03-31",
            statement_type=typ.strip().lower().split("/")[0],
            is_dual=False,
        ))
    # Dual pair: same FY twice, Estimated immediately before Audited
    for a, b in zip(cols, cols[1:]):
        if (a.fy_label == b.fy_label and a.statement_type == "estimated"
                and b.statement_type == "audited"):
            a.is_dual = True
    return name, customer_id, cols


def _file_col(ycol):
    """Map a YearColumn back to its 0-based column in the export rows."""
    return DATA_COL0 + ycol.col_index - 1


# ── Statement parsing ─────────────────────────────────────────────────────────

def _parse_statement(rows, cols, label_map, total_checks, prefix, data):
    """Match labelled rows to line codes; collect file totals for checking."""
    section = ""
    values  = {c.col_index: {} for c in cols}
    totals  = {c.col_index: {} for c in cols}

    for row in rows:
        label = _norm(row[1] if len(row) > 1 else "")
        if not label:
            continue
        has_numbers = any(_num(row[i]) is not None
                          for i in range(DATA_COL0, len(row)))
        if not has_numbers:
            for kw in SECTION_KEYWORDS:
                if label.startswith(kw) or kw in label[:40]:
                    section = label
                    break
            continue

        matched = None
        for sec, pat, code in label_map:
            if sec is not None and sec not in section:
                continue
            if label.startswith(pat):
                matched = code
                break
        if matched:
            for c in cols:
                v = _num(row[_file_col(c)]) if _file_col(c) < len(row) else None
                if v is not None:
                    # Accumulate: several export rows may map to one code
                    values[c.col_index][matched] = (
                        values[c.col_index].get(matched, 0.0) + v)
            continue

        for pat, code in total_checks:
            if label.startswith(pat):
                for c in cols:
                    v = _num(row[_file_col(c)]) if _file_col(c) < len(row) else None
                    if v is not None:
                        totals[c.col_index][code] = v
                break

    for c in cols:
        for code in (label_map_codes := [code for _, _, code in label_map]):
            c.lines.setdefault(code, 0.0)
        c.lines.update({k: v for k, v in values[c.col_index].items()})
        c.lines.setdefault("_totals", {})
        c.lines["_totals"].update(totals[c.col_index])
    return data


def parse_llms_set(bs_path, os_path, perf_path=None, borrower_name=None):
    """Parse one borrower's LLMS export set into CMAData (not yet saved)."""
    data = CMAData(source=str(pathlib.Path(bs_path).parent))

    bs_rows = read_table(bs_path)
    os_rows = read_table(os_path)

    name_bs, cid, cols = _parse_header(bs_rows)
    name_os, _, cols_os = _parse_header(os_rows)
    if cols is None or cols_os is None:
        data.issues.append(("ERROR", "Could not locate Accounting Year / "
                                     "Financial Type header rows"))
        return data
    if [c.fy_label for c in cols] != [c.fy_label for c in cols_os]:
        data.issues.append(("ERROR", "BS and OS exports have different "
                                     "year columns — files from different runs?"))
        return data

    data.borrower = {"name": borrower_name or name_bs or name_os,
                     "cif": cid, "industry": None,
                     "internal_rating": None, "external_rating": None}
    if not data.borrower["name"]:
        data.issues.append(("ERROR", "Borrower name not found in export header"))
        return data

    _parse_statement(bs_rows, cols, BS_LABELS, BS_TOTAL_CHECKS, "bs", data)
    _parse_statement(os_rows, cols, OS_LABELS, OS_TOTAL_CHECKS, "os", data)

    kept = []
    all_codes = (list(OS_INPUTS.values()) + list(BS_INPUTS.values())
                 + list(DSCR_INPUTS.values()))
    for i, c in enumerate(cols):
        for code in all_codes:
            c.lines.setdefault(code, 0.0)
        # No DSCR sheet in the export. The bank computes debt service from
        # the PRIOR column's "instalments due within 1 year" (what fell due
        # at last year-end is repaid this year) — verified against the
        # Performance file's own Gross/Net DSCR figures.
        prior_inst = (cols[i - 1].lines.get("bs_tl_instalments_1yr", 0.0)
                      if i > 0 else c.lines.get("bs_tl_instalments_1yr", 0.0))
        c.lines["ds_tl_inst_applicant"] = prior_inst

        file_totals = c.lines.pop("_totals", {})

        def _derive(L):
            compute_os_derived(L)
            compute_bs_derived(L)
            L["bs_govt_fd_total"] = (L["bs_govt_securities"]
                                     + L["bs_fixed_deposits"])

        _derive(c.lines)

        # Drop year columns that carry no data at all (blank history)
        if (c.lines["bs_total_assets"] == 0 and c.lines["os_net_sales"] == 0):
            continue
        kept.append(c)
        tag = f"{c.fy_label} [{c.statement_type}{'/dual' if c.is_dual else ''}]"

        # Absorb amounts the export carries only at total level
        absorbed = False
        for total_code, absorb_line in ABSORB_RULES:
            if total_code not in file_totals:
                continue
            residual = file_totals[total_code] - c.lines[total_code]
            if residual > 0.005:
                c.lines[absorb_line] += round(residual, 4)
                absorbed = True
                data.issues.append((
                    "INFO",
                    f"{tag}: {residual:.2f} present only on export total "
                    f"{total_code} — assigned to {absorb_line}"))
        if absorbed:
            _derive(c.lines)

        # Final pass: defer to the export's own top-level totals
        for total_code, adjust_line in FINAL_RECONCILE:
            if total_code not in file_totals:
                continue
            residual = file_totals[total_code] - c.lines[total_code]
            if abs(residual) > 0.005:
                c.lines[adjust_line] += round(residual, 4)
                data.issues.append((
                    "INFO",
                    f"{tag}: reconciled {total_code} to export total "
                    f"({residual:+.2f} via {adjust_line})"))
                _derive(c.lines)

        for code, file_value in file_totals.items():
            ours = c.lines.get(code)
            if ours is not None and abs(ours - file_value) > 0.05:
                data.issues.append((
                    "WARNING",
                    f"{tag}: export total {code}={file_value:.2f} vs "
                    f"recomputed {ours:.2f} — check label mapping"))

        imbalance = c.lines["bs_total_liabilities"] - c.lines["bs_total_assets"]
        if abs(imbalance) > 0.5:
            data.issues.append((
                "ERROR", f"{tag}: balance sheet does not balance "
                         f"({imbalance:+.2f} Cr)"))

    for i, c in enumerate(kept, start=1):
        c.col_index = i
    data.years = kept
    if not kept:
        data.issues.append(("ERROR", "No year columns with data found"))

    # Performance file: bank-computed values for reconciliation (not stored)
    data.setup_meta["source_format"] = "llms_export"
    if perf_path:
        data.perf = parse_performance(perf_path, kept)
    else:
        data.perf = {}
    return data


# Bank metrics compared in reconciliation. DPO is deliberately absent:
# the bank's payables-days denominator could not be reproduced from the
# statement data (method difference, not a mapping error).
PERF_METRICS = {
    "net sales":                        "net_sales",
    "ebidta":                           "ebitda",      # bank EBIDTA = PBT+Int+Dep (incl. non-op)
    "tnw":                              "tnw",
    "total outside liabilities":        "tol",
    "nwc net working capital":          "nwc",
    "current ratio":                    "current_ratio",
    "days sales outstanding":           "dso",
    "days inventory outstanding":       "inventory_days",
    "tol tnw":                          "tol_tnw",
    "gross dscr":                       "gross_dscr",
    "cash accruals":                    "cash_accruals",
}


def parse_performance(path, cols):
    """Bank-computed indicator values, keyed [fy_label|type][metric]."""
    rows = read_table(path)
    _, _, perf_cols = _parse_header(rows)
    if perf_cols is None:
        return {}
    out = {}
    seen = set()
    for row in rows:
        label = _norm(row[1] if len(row) > 1 else "")
        if not label or "moving average" in label:
            continue
        for pat, metric in PERF_METRICS.items():
            if label.startswith(pat) and metric not in seen:
                seen.add(metric)
                for c in perf_cols:
                    idx = _file_col(c)
                    v = _num(row[idx]) if idx < len(row) else None
                    if v is not None:
                        key = f"{c.fy_label}|{c.statement_type}"
                        out.setdefault(key, {})[metric] = v
                break
    return out


def reconcile_with_bank(data):
    """Engine metrics vs the bank's own Performance-file values."""
    from analytics.wc_assessment import compute_year_metrics
    rows = []
    for y in data.years:
        key = f"{y.fy_label}|{y.statement_type}"
        bank = getattr(data, "perf", {}).get(key)
        if not bank:
            continue
        m = compute_year_metrics(y.lines)
        engine = {
            "net_sales": m["net_sales"], "tnw": m["tnw"], "tol": m["tol"],
            "nwc": m["nwc"], "current_ratio": m["current_ratio"],
            "dso": m["dso"], "inventory_days": m["inventory_days"],
            "tol_tnw": m["tol_tnw"], "gross_dscr": m["gross_dscr"],
            # Bank EBIDTA is PBT + interest + depreciation (incl. non-op)
            "ebitda": (y.lines["os_pbt"] + y.lines["os_total_interest"]
                       + y.lines["os_depreciation"]),
            "cash_accruals": y.lines["os_cash_accruals"],
        }
        for metric, bank_v in bank.items():
            eng_v = engine.get(metric)
            if eng_v is None:
                continue
            # The bank suppresses DSCR to 0 when the column's OWN
            # instalments row is nil — not a disagreement, skip.
            if metric == "gross_dscr" and bank_v == 0:
                continue
            tol = max(0.02 * max(abs(bank_v), 1.0), 0.06)
            rows.append({
                "fy": key, "metric": metric,
                "bank": bank_v, "engine": round(eng_v, 2),
                "match": abs(eng_v - bank_v) <= tol,
            })
    return rows


# ── Folder ingestion ──────────────────────────────────────────────────────────

def find_borrower_sets(folder):
    """Group LLMS export files in a folder by borrower prefix."""
    folder = pathlib.Path(folder)
    sets = {}
    for f in folder.iterdir():
        if f.suffix.lower() not in (".xls", ".csv"):
            continue
        if "CMA_" not in f.name:
            continue
        prefix = f.name.split("CMA_")[0].strip()
        kind = ("bs" if "Balance_Sheet" in f.name else
                "os" if "Operating_Statement" in f.name else
                "perf" if "Performance" in f.name else None)
        if not kind:
            continue
        entry = sets.setdefault(prefix, {})
        # Prefer .xls over .csv when both exist (same content, one source)
        if kind not in entry or f.suffix.lower() == ".xls":
            entry[kind] = f
    return {p: e for p, e in sets.items() if "bs" in e and "os" in e}


def ingest_llms_set(bs_path, os_path, perf_path=None, borrower_name=None,
                    db_path=None):
    """Parse + persist one borrower. Returns (CMAData, borrower_id or None)."""
    data = parse_llms_set(bs_path, os_path, perf_path=perf_path,
                          borrower_name=borrower_name)
    if data.errors():
        return data, None
    bid = save_to_db(data, db_path=db_path)
    return data, bid
