"""
Excel <-> Python bridge.
Called from Excel VBA via xlwings RunPython.
"""
import xlwings as xw
import sys
import pathlib
import traceback
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"
sys.path.insert(0, str(ROOT / "src"))

from analytics.ohlson import compute_ohlson_from_dict
from analytics.anomaly import run_all_anomaly, save_anomaly_to_db
from analytics.cascade import run_cascade_from_dict, SCENARIOS
from analytics.forecast import run_forecast_summary


def _safe_float(val):
    try:
        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0


def hello_world():
    """Test - confirms bridge is working."""
    try:
        wb    = xw.Book.caller()
        sheet = wb.sheets["0_Setup"]
        sheet.range("A90").value = "Hello from Python!"
        sheet.range("A91").value = "xlwings bridge confirmed."
        sheet.range("A92").value = "Ready for analytics."
    except Exception as e:
        try:
            xw.Book.caller().sheets["0_Setup"].range("A90").value = str(e)
        except Exception:
            pass


def run_ohlson():
    """Reads from SQLite, runs Ohlson, writes results to 0_Setup rows 90+."""
    wb    = xw.Book.caller()
    setup = wb.sheets["0_Setup"]

    try:
        setup.range("A90").value = "run_ohlson: started"

        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row

            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            if not borrower:
                setup.range("A90").value = "ERROR: No borrower in database"
                setup.range("A91").value = "Run: python src/setup_test_borrower.py"
                return

            rows = conn.execute(
                """SELECT * FROM financials
                   WHERE borrower_id = ?
                   ORDER BY fy_end DESC LIMIT 2""",
                (borrower["borrower_id"],)
            ).fetchall()

            if not rows:
                setup.range("A90").value = "ERROR: No financial data in database"
                return

            latest = rows[0]
            prior  = rows[1] if len(rows) > 1 else None

        inputs = {
            "total_assets":        _safe_float(latest["total_assets"]),
            "total_liabilities":   _safe_float(latest["total_liabilities"]),
            "current_assets":      _safe_float(latest["current_assets"]),
            "current_liabilities": _safe_float(latest["current_liabilities"]),
            "working_capital":     _safe_float(latest["working_capital"]),
            "net_income":          _safe_float(latest["net_income"]),
            "ffo":                 _safe_float(latest["ffo"]),
            "net_income_prev":     _safe_float(prior["net_income"]) if prior else None,
        }

        result   = compute_ohlson_from_dict(inputs)
        contribs = result["contributions"]

        sorted_desc = sorted(contribs.items(), key=lambda x: x[1], reverse=True)
        sorted_asc  = sorted(contribs.items(), key=lambda x: x[1])

        setup.range("A90").value = "OHLSON RESULTS"
        setup.range("A91").value = f"Borrower:     {borrower['name']}"
        setup.range("A92").value = f"FY:           {latest['fy_end']}"
        setup.range("A93").value = f"O-Score:      {result['o_score']}"
        setup.range("A94").value = f"Prob Default: {result['prob_default']:.1%}"
        setup.range("A95").value = f"Risk Band:    {result['risk_band']}"
        setup.range("A96").value = (
            f"Top Risk:     {sorted_desc[0][0]}  "
            f"({sorted_desc[0][1]:+.4f})"
        )
        setup.range("A97").value = (
            f"Top Strength: {sorted_asc[0][0]}  "
            f"({sorted_asc[0][1]:+.4f})"
        )
        setup.range("A98").value = (
            f"Inputs: TA={inputs['total_assets']:,.0f}  "
            f"TL={inputs['total_liabilities']:,.0f}  "
            f"NI={inputs['net_income']:,.0f}  "
            f"FFO={inputs['ffo']:,.0f}"
        )
        setup.range("A99").value = (
            f"Source: SQLite | FY: {latest['fy_end']} | Last run: OK"
        )

    except Exception as e:
        setup.range("A90").value = "PYTHON ERROR:"
        setup.range("A91").value = str(e)
        setup.range("A92").value = traceback.format_exc()


def run_anomaly():
    """Runs all 3 anomaly engines, writes results to 0_Setup rows 90+."""
    wb    = xw.Book.caller()
    setup = wb.sheets["0_Setup"]

    try:
        setup.range("A90").value = "Anomaly detection: running..."

        report = run_all_anomaly()
        save_anomaly_to_db(report)

        setup.range("A90").value = "ANOMALY DETECTION RESULTS"
        setup.range("A91").value = f"Borrower:       {report.borrower_name}"
        setup.range("A92").value = f"FY:             {report.fy_end}"
        setup.range("A93").value = f"Master Verdict: {report.master_verdict}"

        if report.beneish:
            setup.range("A95").value = "BENEISH M-SCORE"
            setup.range("A96").value = f"M-Score:  {report.beneish.m_score}"
            setup.range("A97").value = f"Verdict:  {report.beneish.verdict}"

        setup.range("A99").value = "ROLLING Z-SCORES"
        if report.z_scores:
            for i, z in enumerate(report.z_scores):
                setup.range(f"A{100+i}").value = (
                    f"{z.metric:<32} "
                    f"Z={z.z_score:+.2f}  {z.verdict}"
                )
        else:
            setup.range("A100").value = "Need 4+ years of data"

        iso_row = 100 + len(report.z_scores) + 2
        setup.range(f"A{iso_row}").value = "ISOLATION FOREST"
        if report.isoforest:
            setup.range(f"A{iso_row+1}").value = (
                f"Score: {report.isoforest.score}  "
                f"Verdict: {report.isoforest.verdict}"
            )

        setup.range("A89").value = "Last run: Anomaly OK"

    except Exception as e:
        setup.range("A90").value = "PYTHON ERROR:"
        setup.range("A91").value = str(e)
        setup.range("A92").value = traceback.format_exc()

def run_cascade():
    """Reads financials from SQLite, runs cascade + MC, writes to 0_Setup rows 90+."""
    wb    = xw.Book.caller()
    setup = wb.sheets["0_Setup"]

    try:
        setup.range("A90").value = "Cascade: running..."

        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row

            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            if not borrower:
                setup.range("A90").value = "ERROR: No borrower in database"
                return

            latest = conn.execute(
                """SELECT * FROM financials WHERE borrower_id = ?
                   ORDER BY fy_end DESC LIMIT 1""",
                (borrower["borrower_id"],)
            ).fetchone()

            if not latest:
                setup.range("A90").value = "ERROR: No financial data"
                return

        sales    = _safe_float(latest["sales"])
        ebitda   = _safe_float(latest["ebitda"])
        dep      = _safe_float(latest["depreciation"])
        ni       = _safe_float(latest["net_income"])
        tl       = _safe_float(latest["total_liabilities"])
        interest = _safe_float(latest["interest_cost"])

        financials = {
            "sales":         sales,
            "opm":           ebitda / sales if sales > 0 else 0,
            "ebitda":        ebitda,
            "dso":           _safe_float(latest["receivables"]) / sales * 365 if sales > 0 else 0,
            "inv_days":      0,
            "depreciation":  dep,
            "interest":      interest,
            "tl_interest":   interest * 0.6,
            "tl_instalment": 20,
            "stbb":          0,
            "total_debt":    tl,
            "cash_accruals": ni + dep,
        }

        # Run all 6 scenarios
        setup.range("A90").value  = "CAUSAL CASCADE RESULTS"
        setup.range("A91").value  = f"Borrower: {borrower['name']}"
        setup.range("A92").value  = f"FY: {latest['fy_end']}"
        setup.range("A93").value  = "Scenario               Base   P50    P5     P(fail)  Breakeven"

        for i, scenario in enumerate(SCENARIOS):
            r = run_cascade_from_dict(financials, scenario_name=scenario)
            if "error" in r:
                setup.range(f"A{94+i}").value = f"{scenario}: {r['error']}"
                continue

            be = r["breakeven_severity"]
            be_str = f"{be:.0%}" if be is not None else "survives all"

            setup.range(f"A{94+i}").value = (
                f"{scenario:<26} "
                f"{r['dscr_base']:.2f}x  "
                f"{r['dscr_p50']:.2f}x  "
                f"{r['dscr_p5']:.2f}x  "
                f"{r['prob_failure']:.1%}  "
                f"{be_str}"
            )

        # Detailed Recession
        rec = run_cascade_from_dict(financials, scenario_name="Recession")
        setup.range("A101").value = "RECESSION DETAIL"
        setup.range("A102").value = f"Verdict: {rec['verdict']}"
        setup.range("A103").value = f"P5 DSCR: {rec['dscr_p5']:.2f}x"
        setup.range("A104").value = f"Median DSCR: {rec['dscr_p50']:.2f}x"
        setup.range("A105").value = f"Prob failure: {rec['prob_failure']:.1%}"
        setup.range("A106").value = f"Prob covenant breach: {rec['prob_breach']:.1%}"
        setup.range("A107").value = f"Breakeven severity: {rec['breakeven_severity']}"
        setup.range("A89").value  = "Last run: Cascade OK"

    except Exception as e:
        setup.range("A90").value = "PYTHON ERROR:"
        setup.range("A91").value = str(e)
        setup.range("A92").value = traceback.format_exc()

def run_forecast():
    """Runs annual ETS forecast, writes results to 0_Setup rows 90+."""
    wb    = xw.Book.caller()
    setup = wb.sheets["0_Setup"]

    try:
        setup.range("A90").value = "Forecast: running..."

        summary = run_forecast_summary()

        setup.range("A90").value = "FORECAST RESULTS (ETS - Holt-Winters)"
        setup.range("A91").value = f"Borrower: {summary['borrower']}"
        setup.range("A92").value = "Metric                         Yr+1        Yr+2       SMAPE    Verdict"

        for i, entry in enumerate(summary["annual"]):
            if entry["error"]:
                setup.range(f"A{93+i}").value = (
                    f"{entry['metric']:<30} ERROR: {entry['error']}"
                )
                continue

            yr1  = f"{entry['yr1']:>10.1f}" if entry["yr1"] else "        —"
            yr2  = f"{entry['yr2']:>10.1f}" if entry["yr2"] else "        —"
            smape = f"{entry['smape']:.1%}" if entry["smape"] else "—"

            setup.range(f"A{93+i}").value = (
                f"{entry['metric']:<30} "
                f"{yr1}  {yr2}  "
                f"{smape:<8} {entry['verdict']}"
            )

        # Weekly
        wk_row = 93 + len(summary["annual"]) + 1
        setup.range(f"A{wk_row}").value   = "WEEKLY CASH FLOW FORECAST"
        setup.range(f"A{wk_row+1}").value = f"Verdict: {summary['weekly_verdict']}"

        if summary["breach_week"]:
            setup.range(f"A{wk_row+2}").value = (
                f"First cash-negative week: Wk +{summary['breach_week']}"
            )
        if summary["min_cash"] is not None:
            setup.range(f"A{wk_row+3}").value = (
                f"Min projected cash: {summary['min_cash']:.2f} Cr"
            )

        setup.range("A89").value = "Last run: Forecast OK"

    except Exception as e:
        setup.range("A90").value = "PYTHON ERROR:"
        setup.range("A91").value = str(e)
        setup.range("A92").value = traceback.format_exc()