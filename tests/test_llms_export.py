"""Tests for the LLMS export adapter — synthetic fixture, no real data."""
import sqlite3
import pytest

from ingest.llms_export import (
    parse_llms_set, ingest_llms_set, read_table, _parse_header,
)


def _csv(rows):
    import csv
    import io
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


HEADER = [
    ["", "Customer Id : C99999999  Customer Name :SYNTHETIC FOODS PVT LTD"],
    [""],
    ["", "Accounting Year", "2022-23", "2023-24", "2024-25", "2024-25", "2025-26"],
    ["", "Financial Type", "Audited", "Audited", "Estimated", "Audited", "Projected"],
]


def _bs_rows():
    # Columns:        2022-23  2023-24  24-25E  24-25A  2025-26P
    return HEADER + [
        ["Sr.", "Current Liabilities: Short Term Borrowings from Banks"],
        ["1.", "From Applicant Bank",                 2.00, 2.20, 2.50, 2.40, 3.00],
        ["5.", "Total Short Term Borrowings from Banks", 2.00, 2.20, 2.50, 2.40, 3.00],
        ["", "Current Liabilities: Others"],
        ["7.", "Sundry Creditors (Trade-Without LC)", 1.00, 1.10, 1.20, 1.15, 1.40],
        ["13.", "Deposits, Instalments of Term Loans, DPGs etc. (due within one year)",
                                                      0.50, 0.60, 0.70, 0.65, 0.80],
        ["19.", "Total Other Current Liabilities",    1.60, 1.80, 1.90, 1.80, 2.30],
        ["20.", "Total Current Liabilities",          3.60, 4.00, 4.40, 4.20, 5.30],
        ["", "Term Liabilities"],
        ["23.", "Secured Term Loans (excluding instalments payable within 1 year)",
                                                      2.00, 1.50, 1.00, 1.10, 0.50],
        ["31.", "Total Term Liabilities",             2.00, 1.50, 1.00, 1.10, 0.50],
        ["32.", "Total Outside Liabilities",          5.60, 5.50, 5.40, 5.30, 5.80],
        ["", "Net Worth"],
        ["33.", "Equity Share Capital",               1.00, 1.00, 1.00, 1.00, 1.00],
        ["37.", "Surplus/Deficit in Profit & Loss Account",
                                                      0.80, 1.20, 1.70, 1.60, 2.20],
        ["43.", "Total Net Worth",                    1.80, 2.20, 2.70, 2.60, 3.20],
        ["44.", "Total Liabilities",                  7.40, 7.70, 8.10, 7.90, 9.00],
        ["", "Current Assets"],
        ["45.", "Cash & Cash equivalents",            0.30, 0.35, 0.40, 0.38, 0.45],
        ["", "Investments (other than Long Term)"],
        # Carried only at total level (cash excluded — system convention)
        ["49.", "Total Investments (other than Long Term)",
                                                      0.20, 0.20, 0.20, 0.20, 0.20],
        ["", "Other Receivables"],
        ["50.", "Receivables (other than Deferred, Exports, BP & BD)",
                                                      1.50, 1.60, 1.80, 1.70, 2.10],
        ["54.", "Total Other Receivables",            1.50, 1.60, 1.80, 1.70, 2.10],
        ["", "Inventory"],
        ["56.", "Raw Material (Indigenous)",          1.20, 1.30, 1.40, 1.35, 1.60],
        ["63.", "Total Inventory",                    1.20, 1.30, 1.40, 1.35, 1.60],
        ["", "Other Current Assets"],
        ["69.", "Other",                              0.40, 0.45, 0.50, 0.47, 0.55],
        ["70.", "Total Other Current Assets",         0.40, 0.45, 0.50, 0.47, 0.55],
        ["71.", "Total Current Assets",               3.60, 3.90, 4.30, 4.10, 4.90],
        ["", "Fixed Assets"],
        ["72.", "Gross Block",                        5.00, 5.20, 5.40, 5.30, 5.80],
        ["74.", "Depreciation (till date)",           1.20, 1.40, 1.60, 1.50, 1.70],
        ["75.", "Net Block",                          3.80, 3.80, 3.80, 3.80, 4.10],
        ["96.", "Total Assets",                       7.40, 7.70, 8.10, 7.90, 9.00],
    ]


def _os_rows():
    return HEADER + [
        ["1.", "Domestic Sales",                     10.0, 11.0, 12.5, 12.0, 14.0],
        ["7.", "Net Sales",                          10.0, 11.0, 12.5, 12.0, 14.0],
        ["", "Raw Materials: (including Stores & other items used)"],
        ["9.", "Indigenous",                          7.0,  7.6,  8.6,  8.3,  9.6],
        ["14.", "Power & Fuel",                       0.5,  0.55, 0.6,  0.58, 0.7],
        ["17.", "Depreciation & Amortisation",        0.2,  0.2,  0.2,  0.2,  0.2],
        ["24.", "Selling & General Administrative Expenses",
                                                      0.8,  0.9,  1.0,  0.95, 1.1],
        ["26.", "Interest on Working Capital",        0.20, 0.22, 0.25, 0.24, 0.30],
        ["27.", "Interest on Term Loans",             0.18, 0.15, 0.12, 0.13, 0.08],
        ["48.", "Provision for Taxes",                0.28, 0.32, 0.40, 0.38, 0.50],
    ]


@pytest.fixture
def llms_files(tmp_path):
    bs = tmp_path / "X CMA_Balance_Sheet.csv"
    os_ = tmp_path / "X CMA_Operating_Statement.csv"
    bs.write_text(_csv(_bs_rows()), encoding="utf-8")
    os_.write_text(_csv(_os_rows()), encoding="utf-8")
    return bs, os_


def test_header_and_dual_detection(llms_files):
    bs, os_ = llms_files
    name, cid, cols = _parse_header(read_table(bs))
    assert name == "SYNTHETIC FOODS PVT LTD"
    assert cid == "C99999999"
    assert [c.statement_type for c in cols] == \
        ["audited", "audited", "estimated", "audited", "projected"]
    assert cols[2].is_dual and cols[2].fy_label == "2024-25"
    assert not cols[3].is_dual


def test_parse_and_absorption(llms_files):
    bs, os_ = llms_files
    data = parse_llms_set(bs, os_)
    assert data.errors() == []
    assert len(data.years) == 5

    y = data.year("2024-25", "audited")
    # The 0.20 carried only on the investments total row was absorbed
    assert y.lines["bs_fixed_deposits"] == pytest.approx(0.20)
    # Top-level totals defer to the export's own figures
    assert y.lines["bs_tca"] == pytest.approx(4.10, abs=0.01)
    assert y.lines["bs_total_assets"] == pytest.approx(7.90, abs=0.01)
    assert y.lines["bs_total_liabilities"] == pytest.approx(7.90, abs=0.01)


def test_prior_column_dscr_convention(llms_files):
    bs, os_ = llms_files
    data = parse_llms_set(bs, os_)
    # 2025-26 projected uses the 2024-25 AUDITED column's instalments (0.65)
    proj = data.year("2025-26")
    assert proj.lines["ds_tl_inst_applicant"] == pytest.approx(0.65)
    # First column falls back to its own instalments
    first = data.year("2022-23")
    assert first.lines["ds_tl_inst_applicant"] == pytest.approx(0.50)


def test_ingest_to_db_and_assess(llms_files, temp_db):
    bs, os_ = llms_files
    data, bid = ingest_llms_set(bs, os_, db_path=temp_db)
    assert bid is not None
    with sqlite3.connect(temp_db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM cma_statement WHERE borrower_id=?",
            (bid,)).fetchone()[0]
        fin = conn.execute(
            "SELECT COUNT(*) FROM financials WHERE borrower_id=?",
            (bid,)).fetchone()[0]
    assert n == 5
    assert fin == 3       # three audited years

    from analytics.wc_assessment import assess_working_capital
    a = assess_working_capital("SYNTHETIC FOODS PVT LTD", db_path=temp_db)
    assert a.error == ""
    assert a.latest_audited["fy_label"] == "2024-25"
    assert a.latest_audited["metrics"]["gross_dscr"] is not None


def test_blank_columns_dropped(tmp_path):
    rows_bs = _bs_rows()
    rows_os = _os_rows()
    # Blank out the first audited column entirely (cols index 2)
    for rows in (rows_bs, rows_os):
        for r in rows[4:]:
            if len(r) > 2:
                r[2] = ""
    bs = tmp_path / "Y CMA_Balance_Sheet.csv"
    os_ = tmp_path / "Y CMA_Operating_Statement.csv"
    bs.write_text(_csv(rows_bs), encoding="utf-8")
    os_.write_text(_csv(rows_os), encoding="utf-8")

    data = parse_llms_set(bs, os_)
    assert data.errors() == []
    assert [y.fy_label for y in data.years] == \
        ["2023-24", "2024-25", "2024-25", "2025-26"]
