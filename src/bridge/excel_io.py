"""
Excel <-> Python bridge.
Called from Excel VBA via xlwings RunPython.

Output layout on Python_Output sheet:
  Col A-C  : Ohlson O-Score
  Col E-G  : Anomaly Detection
  Col I-K  : Causal Cascade
  Col M-O  : Forecasting
  Col Q-S  : RBI Feeds
  Col A-C  : Narrative (rows 60+)
"""
import xlwings as xw
import sys
import pathlib
import traceback
import sqlite3
import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"
sys.path.insert(0, str(ROOT / "src"))

from analytics.ohlson  import compute_ohlson_from_dict
from analytics.anomaly import run_all_anomaly, save_anomaly_to_db
from analytics.cascade import run_cascade_from_dict, SCENARIOS
from analytics.forecast import run_forecast_summary
from agents.committee  import run_committee
from data.feeds        import (
    fetch_rbi_updates, save_rbi_to_db, get_rbi_cached
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(val):
    try:
        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0


def _get_output_sheet():
    """Return the Python_Output sheet."""
    wb = xw.Book.caller()
    return wb.sheets["Python_Output"]


def _write_header(sh, col, title):
    """Write a section header in the output sheet."""
    sh.range(f"{col}1").value  = title
    sh.range(f"{col}2").value  = f"Last run: {datetime.datetime.now():%d-%b-%Y %H:%M}"


def _clear_col(sh, col, rows=50):
    """Clear a column range before writing (single COM call, not per-cell)."""
    sh.range(f"{col}1:{col}{rows}").clear_contents()


def _write_error_to_sheet(sh, col, e):
    """Write error details to output sheet."""
    sh.range(f"{col}1").value = "PYTHON ERROR:"
    sh.range(f"{col}2").value = str(e)
    sh.range(f"{col}3").value = traceback.format_exc()


# ── Hello World ───────────────────────────────────────────────────────────────

def hello_world():
    """Test - confirms bridge is working."""
    try:
        sh = _get_output_sheet()
        sh.range("A1").value = "Hello from Python!"
        sh.range("A2").value = "xlwings bridge confirmed."
        sh.range("A3").value = f"Time: {datetime.datetime.now():%d-%b-%Y %H:%M:%S}"
    except Exception as e:
        try:
            _get_output_sheet().range("A1").value = str(e)
        except Exception:
            pass


# ── Module 1: Ohlson (Col A) ──────────────────────────────────────────────────

def run_ohlson():
    """Runs Ohlson O-Score. Output: Python_Output col A."""
    try:
        sh = _get_output_sheet()
        _clear_col(sh, "A")
        _write_header(sh, "A", "OHLSON O-SCORE")

        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            if not borrower:
                sh.range("A3").value = "No borrower in database"
                return

            rows = conn.execute(
                """SELECT * FROM financials WHERE borrower_id=?
                   ORDER BY fy_end DESC LIMIT 2""",
                (borrower["borrower_id"],)
            ).fetchall()

            if not rows:
                sh.range("A3").value = "No financial data in database"
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

        result = compute_ohlson_from_dict(inputs)
        if "error" in result:
            sh.range("A3").value = f"ERROR: {result['error']}"
            return
        contribs = result["contributions"]
        sorted_desc = sorted(contribs.items(), key=lambda x: x[1], reverse=True)
        sorted_asc  = sorted(contribs.items(), key=lambda x: x[1])

        sh.range("A3").value  = f"Borrower:     {borrower['name']}"
        sh.range("A4").value  = f"FY:           {latest['fy_end']}"
        sh.range("A5").value  = f"O-Score:      {result['o_score']}"
        sh.range("A6").value  = f"Prob Default: {result['prob_default']:.1%}"
        sh.range("A7").value  = f"Risk Band:    {result['risk_band']}"
        sh.range("A8").value  = f"Top Risk:     {sorted_desc[0][0]} ({sorted_desc[0][1]:+.4f})"
        sh.range("A9").value  = f"Top Strength: {sorted_asc[0][0]} ({sorted_asc[0][1]:+.4f})"
        sh.range("A11").value = "Contributions:"
        for i, (k, v) in enumerate(contribs.items()):
            sh.range(f"A{12+i}").value = f"  {k:<10} {v:+.4f}"

        sh.range("A23").value = (
            f"TA={inputs['total_assets']:,.0f}  "
            f"TL={inputs['total_liabilities']:,.0f}  "
            f"NI={inputs['net_income']:,.0f}"
        )

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "A", e)


# ── Module 2: Anomaly (Col E) ─────────────────────────────────────────────────

def run_anomaly():
    """Runs anomaly detection. Output: Python_Output col E."""
    try:
        sh = _get_output_sheet()
        _clear_col(sh, "E")
        _write_header(sh, "E", "ANOMALY DETECTION")

        report = run_all_anomaly()
        save_anomaly_to_db(report)

        sh.range("E3").value = f"Borrower: {report.borrower_name}"
        sh.range("E4").value = f"FY:       {report.fy_end}"
        sh.range("E5").value = f"Master:   {report.master_verdict}"

        sh.range("E7").value = "BENEISH M-SCORE"
        if report.beneish:
            sh.range("E8").value  = f"M-Score: {report.beneish.m_score}"
            sh.range("E9").value  = f"Verdict: {report.beneish.verdict}"
            sh.range("E11").value = "Components:"
            for i, (k, v) in enumerate(report.beneish.components.items()):
                sh.range(f"E{12+i}").value = f"  {k:<6} {v:+.4f}"

        sh.range("E21").value = "ROLLING Z-SCORES"
        if report.z_scores:
            for i, z in enumerate(report.z_scores):
                sh.range(f"E{22+i}").value = (
                    f"{z.metric:<32} "
                    f"Z={z.z_score:+.2f}  {z.verdict}"
                )
        else:
            sh.range("E22").value = "Need 4+ years of data"

        iso_row = 22 + max(len(report.z_scores), 1) + 2
        sh.range(f"E{iso_row}").value   = "ISOLATION FOREST"
        sh.range(f"E{iso_row+1}").value = (
            report.isoforest.verdict if report.isoforest
            else "Not run"
        )

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "E", e)


# ── Module 3: Cascade (Col I) ─────────────────────────────────────────────────

def run_cascade():
    """Runs causal cascade. Output: Python_Output col I."""
    try:
        sh = _get_output_sheet()
        _clear_col(sh, "I")
        _write_header(sh, "I", "CAUSAL CASCADE + MONTE CARLO")

        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not borrower:
                sh.range("I3").value = "No borrower in database"
                return
            latest = conn.execute(
                """SELECT * FROM financials WHERE borrower_id=?
                   ORDER BY fy_end DESC LIMIT 1""",
                (borrower["borrower_id"],)
            ).fetchone()

        if not latest:
            sh.range("I3").value = "No financial data"
            return

        sales   = _safe_float(latest["sales"])
        ebitda  = _safe_float(latest["ebitda"])
        dep     = _safe_float(latest["depreciation"])
        ni      = _safe_float(latest["net_income"])
        tl      = _safe_float(latest["total_liabilities"])
        interest= _safe_float(latest["interest_cost"])

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

        sh.range("I3").value  = f"Borrower: {borrower['name']}"
        sh.range("I4").value  = f"FY:       {latest['fy_end']}"
        sh.range("I5").value  = "Scenario               Base   P50    P5     P(fail)"

        for i, scenario in enumerate(SCENARIOS):
            r = run_cascade_from_dict(financials, scenario_name=scenario)
            if "error" in r:
                sh.range(f"I{6+i}").value = f"{scenario}: {r['error']}"
                continue
            sh.range(f"I{6+i}").value = (
                f"{scenario:<26} "
                f"{r['dscr_base']:.2f}x  "
                f"{r['dscr_p50']:.2f}x  "
                f"{r['dscr_p5']:.2f}x  "
                f"{r['prob_failure']:.1%}"
            )

        rec = run_cascade_from_dict(financials, scenario_name="Recession")
        sh.range("I14").value = "RECESSION DETAIL"
        if "error" in rec:
            sh.range("I15").value = f"ERROR: {rec['error']}"
            return
        sh.range("I15").value = f"Verdict:  {rec['verdict']}"
        sh.range("I16").value = f"P5 DSCR:  {rec['dscr_p5']:.2f}x"
        sh.range("I17").value = f"Median:   {rec['dscr_p50']:.2f}x"
        sh.range("I18").value = f"P(fail):  {rec['prob_failure']:.1%}"
        sh.range("I19").value = f"Breakeven:{rec['breakeven_severity']}"

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "I", e)


# ── Module 4: Forecast (Col M) ────────────────────────────────────────────────

def run_forecast():
    """Runs ETS forecast. Output: Python_Output col M."""
    try:
        sh = _get_output_sheet()
        _clear_col(sh, "M")
        _write_header(sh, "M", "FORECAST (ETS Holt-Winters)")

        summary = run_forecast_summary()

        sh.range("M3").value  = "Metric                         Yr+1        Yr+2     SMAPE"
        for i, entry in enumerate(summary["annual"]):
            if entry["error"]:
                sh.range(f"M{4+i}").value = f"{entry['metric']:<30} ERROR"
                continue
            yr1   = f"{entry['yr1']:>10.1f}" if entry["yr1"] else "         —"
            yr2   = f"{entry['yr2']:>10.1f}" if entry["yr2"] else "         —"
            smape = f"{entry['smape']:.1%}" if entry["smape"] else "—"
            sh.range(f"M{4+i}").value = (
                f"{entry['metric']:<30} {yr1}  {yr2}  {smape}"
            )

        wk_row = 4 + len(summary["annual"]) + 2
        sh.range(f"M{wk_row}").value   = "WEEKLY CASH FLOW"
        sh.range(f"M{wk_row+1}").value = summary["weekly_verdict"]

        if summary["breach_week"]:
            sh.range(f"M{wk_row+2}").value = (
                f"First breach: Wk +{summary['breach_week']}"
            )
        if summary["min_cash"] is not None:
            sh.range(f"M{wk_row+3}").value = (
                f"Min cash: {summary['min_cash']:.2f} Cr"
            )

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "M", e)


# ── Module 5: Feeds (Col Q) ───────────────────────────────────────────────────

def run_feeds():
    """Fetches RBI data. Output: Python_Output col Q."""
    try:
        sh = _get_output_sheet()
        _clear_col(sh, "Q")
        _write_header(sh, "Q", "RBI REGULATORY UPDATES")

        items, err = fetch_rbi_updates("notifications", since_days=30)
        if not err:
            save_rbi_to_db(items)

        cached = get_rbi_cached(limit=10)

        sh.range("Q3").value = f"Items fetched: {len(items) if not err else 0}"
        sh.range("Q4").value = f"Items in cache: {len(cached)}"
        sh.range("Q5").value = "Latest notifications:"

        for i, item in enumerate(cached):
            sh.range(f"Q{7+i}").value = (
                f"{item['published'][:10]}  "
                f"{item['title'][:75]}"
            )

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "Q", e)


# ── Module 6: CMA ingestion + WC assessment (Col U) ──────────────────────────

def run_cma_assessment():
    """
    Ingests the CMA sheets of the CALLING workbook (live values, unsaved
    edits included), persists them to SQLite, runs the Form V / DSCR /
    ratio assessment and projection scrutiny. Output: Python_Output col U.
    """
    try:
        from ingest.cma_workbook import parse_grids, load_grids_xlwings, save_to_db
        from analytics.wc_assessment import assess_working_capital, form_v
        from analytics.forecast import scrutinize_projections

        wb = xw.Book.caller()
        sh = _get_output_sheet()
        _clear_col(sh, "U", rows=70)
        _write_header(sh, "U", "CMA INGESTION + WC ASSESSMENT")

        data = parse_grids(load_grids_xlwings(wb), source=wb.fullname)
        if data.errors():
            sh.range("U3").value = "INGESTION ERRORS:"
            for i, msg in enumerate(data.errors()[:10]):
                sh.range(f"U{4+i}").value = f"  {msg}"
            return
        save_to_db(data)

        warnings = [m for lvl, m in data.issues if lvl == "WARNING"]
        sh.range("U3").value = f"Borrower: {data.borrower['name']}"
        sh.range("U4").value = (f"Ingested {len(data.years)} year-columns"
                                f"  ({len(warnings)} warnings)")

        a = assess_working_capital(data.borrower["name"])
        basis_year, basis_label = a.assessment_basis
        row = 6
        sh.range(f"U{row}").value = (
            f"FORM V — MPBF  ({basis_year['fy_label']} "
            f"{basis_label.split(' — ')[0]})" if basis_year
            else "FORM V — no usable year")
        for label, value in form_v(a):
            row += 1
            sh.range(f"U{row}").value = f"  {label:<48} {value:>12,.2f}"

        row += 2
        sh.range(f"U{row}").value = f"Average Gross DSCR: {a.dscr_avg_gross}x"
        for y in a.years:
            m = y["metrics"]
            if m["gross_dscr"] is None or y["is_dual"]:
                continue
            row += 1
            sh.range(f"U{row}").value = (
                f"  {y['fy_label']} [{y['statement_type'][:4]}] "
                f"Gross={m['gross_dscr']:.2f}x  CR={m['current_ratio']}")

        breaches = [v for v in a.verdicts if v["status"] == "BREACH"]
        row += 2
        sh.range(f"U{row}").value = f"THRESHOLD BREACHES: {len(breaches)}"
        for v in breaches[:8]:
            row += 1
            sh.range(f"U{row}").value = (
                f"  {v['fy_label']} {v['metric']}: {v['value']} "
                f"(req {v['direction']} {v['threshold']})")

        row += 2
        sh.range(f"U{row}").value = f"RED FLAGS: {len(a.red_flags) or 'none'}"
        for flag in a.red_flags[:8]:
            row += 1
            sh.range(f"U{row}").value = f"  {flag}"

        row += 2
        sh.range(f"U{row}").value = "PROJECTION SCRUTINY (vs ETS bands)"
        scr = scrutinize_projections(data.borrower["name"])
        for metric, entry in scr["metrics"].items():
            row += 1
            if entry["error"]:
                sh.range(f"U{row}").value = f"  {metric}: {entry['error']}"
                continue
            verdicts = [p["verdict"] for p in entry["projections"]]
            worst = ("AGGRESSIVE" if "AGGRESSIVE" in verdicts else
                     "OPTIMISTIC" if "OPTIMISTIC" in verdicts else "IN LINE")
            sh.range(f"U{row}").value = (
                f"  {metric}: {worst}  "
                f"({', '.join(verdicts)})")

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "U", e)


# ── Module 7: EWS / Red Flags (Col W) ─────────────────────────────────────────

def run_ews_flags():
    """Runs the 32 red flags + 13 exception tests + RBI EWS on the most
    recently ingested CMA data. Output: Python_Output col W."""
    try:
        from analytics.ews import run_full_ews

        sh = _get_output_sheet()
        _clear_col(sh, "W", rows=70)
        _write_header(sh, "W", "EWS / RED FLAGS")

        r = run_full_ews()
        if r.error:
            sh.range("W3").value = f"ERROR: {r.error}"
            return

        sh.range("W3").value = f"Borrower: {r.borrower_name}"
        sh.range("W4").value = f"Verdict:  {r.red_flag_verdict}"
        sh.range("W5").value = (
            f"RFA: {'REQUIRED — ' + r.rfa_reason if r.rfa_required else 'not indicated'}")

        row = 7
        sh.range(f"W{row}").value = (
            f"RED FLAGS TRIGGERED: {len(r.triggered_red_flags)}/32")
        for f in r.triggered_red_flags[:15]:
            row += 1
            sh.range(f"W{row}").value = (
                f"  {f.rule_id} [{f.status}] {f.test} = {f.value}")

        row += 2
        sh.range(f"W{row}").value = (
            f"EXCEPTION TESTS TRIGGERED: {len(r.triggered_exceptions)}/13")
        for f in r.triggered_exceptions[:10]:
            row += 1
            sh.range(f"W{row}").value = (
                f"  {f.rule_id} {f.test} = {f.value}")
            row += 1
            sh.range(f"W{row}").value = f"     {f.detail[:90]}"

        ews_hits = [i for i in r.ews_indicators if i["triggered"]]
        row += 2
        sh.range(f"W{row}").value = f"RBI EWS INDICATORS TRIGGERED: {len(ews_hits)}"
        for i in ews_hits[:10]:
            row += 1
            sh.range(f"W{row}").value = (
                f"  #{i['id']} [{i['severity']}] {i['indicator'][:70]}")

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "W", e)


# ── Module 8: Narrative (Col A, rows 60+) ────────────────────────────────────

def run_narrative():
    """Runs 4-agent committee. Output: Python_Output col A rows 60+."""
    try:
        sh = _get_output_sheet()

        # Clear narrative area only
        for r in range(58, 120):
            sh.range(f"A{r}").value = None

        sh.range("A58").value = "CREDIT COMMITTEE NARRATIVE"
        sh.range("A59").value = f"Started: {datetime.datetime.now():%d-%b-%Y %H:%M}"
        sh.range("A60").value = "Running 4 agents (60-120 seconds)..."

        result = run_committee()

        if result.error:
            sh.range("A60").value = f"ERROR: {result.error}"
            return

        sh.range("A60").value = f"Borrower:   {result.borrower_name}"
        sh.range("A61").value = f"Verdict:    {result.verdict}"
        sh.range("A62").value = f"Confidence: {result.confidence}"
        sh.range("A63").value = f"Key Risk:   {result.key_risk}"
        sh.range("A65").value = "CREDIT JUSTIFICATION MEMORANDUM"

        paragraphs = result.final_memo.split("\n\n")
        row = 66
        for para in paragraphs:
            if para.strip():
                sh.range(f"A{row}").value = para.strip()
                row += 2

        sh.range(f"A{row+1}").value = "AUDIT LOG"
        for j, entry in enumerate(result.audit_log):
            status = "OK" if not entry["error"] else f"ERROR: {entry['error']}"
            sh.range(f"A{row+2+j}").value = (
                f"{entry['agent']:<12} "
                f"{entry['model']:<22} "
                f"{status}"
            )

    except Exception as e:
        _write_error_to_sheet(_get_output_sheet(), "A", e)