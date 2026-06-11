"""
Fills a copy of the CMA template with an internally consistent synthetic
borrower ("Bharat Precision Components Pvt Ltd"). Used by the pytest suite
and the demo script — and handy for training.

The balance sheet balances by construction: the P&L surplus is the plug
that equates total liabilities to total assets, exactly as retained
earnings do in real life.
"""
import pathlib
import shutil

from ingest.cma_workbook import (
    SHEET_SETUP, SHEET_OS, SHEET_BS, SHEET_DSCR,
    OS_INPUTS, BS_INPUTS, DSCR_INPUTS, FIRST_YEAR_COL,
    build_year_columns, compute_os_derived, compute_bs_derived,
)

ROOT     = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "CMA_Auto_Analytics_v9.xlsx"

BORROWER = "Bharat Precision Components Pvt Ltd"

# Net-sales path across the 9 columns of the default 2022/2029/2025 config:
# 2 audited, dual estimate, latest audited, current estimate, 4 projections.
SALES_PATH = [1500.0, 1650.0, 1790.0, 1815.0, 1995.0,
              2195.0, 2414.0, 2656.0, 2921.0]


def synth_year_inputs(sales, i):
    """Raw input cells for one year column, driven off net sales."""
    L = {code: 0.0 for code in
         list(OS_INPUTS.values()) + list(BS_INPUTS.values())
         + list(DSCR_INPUTS.values())}

    # ── Operating statement ──
    L["os_domestic_sales"] = round(sales * 0.80, 2)
    L["os_export_sales"]   = round(sales * 0.20, 2)
    L["os_rm_imported"]    = round(sales * 0.05, 2)
    L["os_rm_indigenous"]  = round(sales * 0.55, 2)
    L["os_spares_indigenous"] = round(sales * 0.01, 2)
    L["os_power_fuel"]     = round(sales * 0.04, 2)
    L["os_direct_labour"]  = round(sales * 0.06, 2)
    L["os_other_mfg"]      = round(sales * 0.02, 2)
    L["os_depreciation"]   = 25.0 + 2.0 * i
    L["os_opening_sip"]    = round(sales * 0.010, 2)
    L["os_closing_sip"]    = round(sales * 0.011, 2)
    L["os_opening_fg"]     = round(sales * 0.020, 2)
    L["os_closing_fg"]     = round(sales * 0.022, 2)
    L["os_sga"]            = round(sales * 0.08, 2)
    L["os_interest_wc"]    = 12.0 + i
    L["os_interest_tl"]    = max(8.0 - i, 2.0)
    L["os_other_finance"]  = 1.0
    L["os_income_investments"] = 2.0
    L["os_forex_loss"]     = 1.0
    L["os_prior_period"]   = 0.0
    L["os_dividend_paid"]  = 0.0

    # Tax = 25% of PBT (needs PBT, so derive once with tax=0, then refresh)
    compute_os_derived(L)
    L["os_tax_provision"] = round(max(L["os_pbt"], 0.0) * 0.25, 2)
    compute_os_derived(L)

    # ── Balance sheet ──
    L["bs_stbb_applicant"]   = round(sales * 0.12, 2)
    L["bs_creditors_trade"]  = round(sales * 0.08, 2)
    L["bs_customer_advances"] = round(sales * 0.010, 2)
    L["bs_provision_tax"]    = round(sales * 0.010, 2)
    L["bs_statutory_dues"]   = round(sales * 0.005, 2)
    L["bs_tl_instalments_1yr"] = 10.0
    L["bs_secured_tl"]       = max(80.0 - 8.0 * i, 0.0)
    L["bs_unsecured_promoters"] = 20.0
    L["bs_dtl"]              = 5.0
    L["bs_equity_capital"]   = 100.0
    L["bs_general_reserve"]  = 50.0
    L["bs_security_premium"] = 10.0
    L["bs_cash"]             = round(sales * 0.02, 2)
    L["bs_govt_securities"]  = 2.0
    L["bs_fixed_deposits"]   = 5.0
    L["bs_receivables_domestic"] = round(sales * 0.10, 2)
    L["bs_receivables_export"]   = round(sales * 0.02, 2)
    L["bs_rm_imported"]      = round(sales * 0.010, 2)
    L["bs_rm_indigenous"]    = round(sales * 0.080, 2)
    L["bs_sip"]              = round(sales * 0.012, 2)
    L["bs_fg"]               = round(sales * 0.020, 2)
    L["bs_spares_indigenous"] = round(sales * 0.005, 2)
    L["bs_adv_suppliers_rm"] = round(sales * 0.010, 2)
    L["bs_adv_tax"]          = round(sales * 0.008, 2)
    L["bs_gst_input"]        = round(sales * 0.005, 2)
    L["bs_other_oca"]        = round(sales * 0.002, 2)
    L["bs_gross_block"]      = 400.0 + 30.0 * i
    L["bs_cwip"]             = 10.0
    L["bs_acc_depreciation"] = 120.0 + 25.0 * i
    L["bs_inv_group_cos"]    = 5.0
    L["bs_deposits"]         = 3.0
    L["bs_other_nca"]        = 2.0
    L["bs_gross_intangibles"] = 8.0
    L["bs_amortisation"]     = 4.0
    L["bs_dta"]              = 2.0

    # Balance the sheet: P&L surplus is the plug
    L["bs_pl_surplus"] = 0.0
    compute_bs_derived(L)
    L["bs_pl_surplus"] = round(
        L["bs_total_assets"] - L["bs_total_liabilities"], 2)
    compute_bs_derived(L)

    # ── DSCR sheet inputs ──
    L["ds_tl_inst_applicant"] = 10.0
    L["ds_tl_inst_other"]     = 0.0
    L["ds_accruals_used"]     = 0.0

    return L


def build_demo_workbook(out_path, template=TEMPLATE, sales_path=None,
                        first_year=2022, last_year=2029, current_year=2025):
    """
    Copy the template and fill it with the synthetic borrower.
    Returns {fy_label_with_type: input-lines dict} for independent
    verification in tests.
    """
    import openpyxl

    sales_path = sales_path or SALES_PATH
    cols = build_year_columns(first_year, last_year, current_year)
    if len(sales_path) != len(cols):
        raise ValueError(f"sales_path needs {len(cols)} values "
                         f"(got {len(sales_path)})")

    out_path = pathlib.Path(out_path)
    shutil.copy(template, out_path)

    wb = openpyxl.load_workbook(out_path)
    setup = wb[SHEET_SETUP]
    setup["C5"]  = BORROWER
    setup["C6"]  = "CIF0012345"
    setup["C7"]  = "Auto Components"
    setup["C12"] = first_year
    setup["C13"] = last_year
    setup["C14"] = current_year
    setup["B20"] = 180.0       # existing FB-WC limit
    setup["D20"] = 220.0       # proposed FB-WC limit

    expected = {}
    sheets = {SHEET_OS: OS_INPUTS, SHEET_BS: BS_INPUTS, SHEET_DSCR: DSCR_INPUTS}
    for ycol, sales in zip(cols, sales_path):
        L = synth_year_inputs(sales, ycol.col_index - 1)
        col = FIRST_YEAR_COL + ycol.col_index - 1
        for sheet_name, registry in sheets.items():
            ws = wb[sheet_name]
            for row, code in registry.items():
                ws.cell(row=row, column=col, value=L[code])
        expected[f"{ycol.fy_label}|{ycol.statement_type}"
                 f"{'|dual' if ycol.is_dual else ''}"] = L

    wb.save(out_path)
    wb.close()
    return expected
