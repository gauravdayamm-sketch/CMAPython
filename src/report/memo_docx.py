"""
Credit Memorandum Word export — assembles everything the pipeline knows
about a borrower into a bank-format .docx:

  1. Borrower snapshot + proposal limits
  2. Distress score ensemble
  3. Form V working capital assessment (MPBF) + DSCR
  4. Key ratios vs thresholds
  5. Red flags / exception engine / RBI EWS
  6. Projection scrutiny
  7. AI committee narrative (latest from the narrative table), with the
     mandatory analyst-review disclaimer

Every quantitative section is computed fresh from the database at export
time — the document never goes stale relative to the data.
"""
import datetime
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"

DISCLAIMER = ("AI-DRAFTED DOCUMENT — REQUIRES ANALYST REVIEW AND SIGN-OFF "
              "BEFORE SUBMISSION.")


def _heading(doc, text, level=1):
    doc.add_heading(text, level=level)


def _table(doc, rows, header=None, widths=None):
    from docx.shared import Pt
    n_cols = len(header) if header else len(rows[0])
    table = doc.add_table(rows=0, cols=n_cols)
    table.style = "Light Grid Accent 1"
    if header:
        cells = table.add_row().cells
        for c, text in zip(cells, header):
            run = c.paragraphs[0].add_run(str(text))
            run.bold = True
            run.font.size = Pt(9)
    for row in rows:
        cells = table.add_row().cells
        for c, value in zip(cells, row):
            run = c.paragraphs[0].add_run(
                "" if value is None else str(value))
            run.font.size = Pt(9)
    return table


def _fmt(v, pattern="{:,.2f}"):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return pattern.format(v)
    return str(v)


def _latest_narrative(borrower_name, db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT n.output_text, n.model, n.run_ts FROM narrative n
            JOIN borrower b ON b.borrower_id = n.borrower_id
            WHERE b.name = ? AND n.agent = 'underwriter'
            ORDER BY n.run_ts DESC, n.narr_id DESC LIMIT 1
        """, (borrower_name,)).fetchone()
    return row


def generate_credit_memo(borrower_name=None, out_path=None, db_path=None):
    """Build the .docx. Returns the output path."""
    from docx import Document
    from docx.shared import Pt

    from analytics.wc_assessment import assess_working_capital, form_v
    from analytics.ews import run_full_ews
    from analytics.scores import run_ensemble
    from analytics.forecast import scrutinize_projections

    db = db_path or DB
    a = assess_working_capital(borrower_name, db_path=db)
    if a.error:
        raise ValueError(a.error)
    name = a.borrower_name

    ews = run_full_ews(name, db_path=db)
    ens = run_ensemble(name, db_path=db)
    scr = scrutinize_projections(name, db_path=db)

    out_path = pathlib.Path(out_path or ROOT / "data" /
                            f"Credit_Memo_{name.replace(' ', '_')}.docx")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    doc.add_heading("CREDIT JUSTIFICATION MEMORANDUM", level=0)
    p = doc.add_paragraph()
    p.add_run(f"{name}\n").bold = True
    p.add_run(f"Generated: {datetime.datetime.now():%d %B %Y %H:%M}   |   "
              f"CMA Auto-Analytics pipeline")
    doc.add_paragraph(DISCLAIMER).runs[0].bold = True

    # 1 ── Snapshot
    _heading(doc, "1. Borrower Snapshot & Proposal")
    latest = a.latest_audited
    snap = [
        ("Latest audited FY", latest["fy_label"] if latest else "—"),
        ("Net sales (₹ Cr)", _fmt(latest["metrics"]["net_sales"]) if latest else "—"),
        ("TNW (₹ Cr)", _fmt(latest["metrics"]["tnw"]) if latest else "—"),
        ("PAT (₹ Cr)", _fmt(latest["metrics"]["pat"]) if latest else "—"),
    ]
    for facility, lim in a.proposals.items():
        snap.append((f"Limit — {facility}",
                     f"existing {_fmt(lim['existing'])} / "
                     f"proposed {_fmt(lim['proposed'])}"))
    _table(doc, snap, header=("Item", "Value"))

    # 2 ── Score ensemble
    _heading(doc, "2. Distress Score Ensemble")
    if not ens.error:
        rows = []
        for s in (ens.ohlson, ens.altman, ens.zmijewski):
            if s:
                rows.append((s.model, _fmt(s.score, "{:.4f}"),
                             f"{s.prob:.1%}" if s.prob is not None else "—",
                             s.zone, s.note or ""))
        _table(doc, rows, header=("Model", "Score", "Prob.", "Zone", "Note"))
        doc.add_paragraph(f"Ensemble verdict: {ens.verdict}")
    else:
        doc.add_paragraph(f"Not available: {ens.error}")

    # 3 ── Working capital + DSCR
    _heading(doc, "3. Working Capital Assessment (Form V)")
    _table(doc, [(label, _fmt(value)) for label, value in form_v(a)],
           header=("Form V item", "₹ Cr"))
    doc.add_paragraph()
    dscr_rows = [
        (y["fy_label"], y["statement_type"],
         _fmt(y["metrics"]["gross_dscr"], "{:.2f}"),
         _fmt(y["metrics"]["net_dscr"], "{:.2f}"))
        for y in a.years
        if y["metrics"]["gross_dscr"] is not None and not y["is_dual"]
    ]
    if dscr_rows:
        _table(doc, dscr_rows, header=("FY", "Type", "Gross DSCR", "Net DSCR"))
        doc.add_paragraph(f"Average gross DSCR: {_fmt(a.dscr_avg_gross, '{:.2f}')}x")

    # 4 ── Ratios vs thresholds
    _heading(doc, "4. Key Ratios vs Sanction Thresholds")
    breaches = [v for v in a.verdicts if v["status"] == "BREACH"]
    latest_verdicts = [v for v in a.verdicts
                       if latest and v["fy_label"] == latest["fy_label"]]
    _table(doc,
           [(v["metric"], _fmt(v["value"], "{:.4f}"),
             f"{v['direction']} {v['threshold']}", v["status"])
            for v in latest_verdicts],
           header=("Ratio (latest audited)", "Value", "Threshold", "Status"))
    doc.add_paragraph(
        f"Threshold breaches across all years: {len(breaches)}")

    # 5 ── Red flags / EWS
    _heading(doc, "5. Red Flags, Exceptions & RBI EWS")
    if not ews.error:
        doc.add_paragraph(f"Red flag verdict: {ews.red_flag_verdict}")
        doc.add_paragraph(
            "RFA (Red Flagged Account): "
            + (f"REQUIRED — {ews.rfa_reason}" if ews.rfa_required
               else "not indicated"))
        hits = ews.triggered_red_flags + ews.triggered_exceptions
        if hits:
            _table(doc,
                   [(f.rule_id, f.test, _fmt(f.value, "{:.4f}"), f.detail[:120])
                    for f in hits],
                   header=("Rule", "Test", "Value", "Action required"))
        ews_hits = [i for i in ews.ews_indicators if i["triggered"]]
        if ews_hits:
            _table(doc,
                   [(f"#{i['id']}", i["indicator"], i["severity"], i["source"])
                    for i in ews_hits],
                   header=("EWS", "Indicator", "Severity", "Source"))
    else:
        doc.add_paragraph(f"Not available: {ews.error}")

    # 6 ── Projection scrutiny
    _heading(doc, "6. Projection Scrutiny (vs ETS bands)")
    if not scr["error"]:
        for metric, entry in scr["metrics"].items():
            if entry["error"]:
                doc.add_paragraph(f"{metric}: {entry['error']}")
                continue
            cagr = ""
            if entry["hist_cagr"] is not None and entry["proj_cagr"] is not None:
                cagr = (f" — historical CAGR {entry['hist_cagr']:.1%}, "
                        f"projected {entry['proj_cagr']:.1%}")
            doc.add_paragraph(f"{metric}{cagr}", style="Intense Quote")
            _table(doc,
                   [(pp["fy_label"], _fmt(pp["value"]),
                     f"{_fmt(pp['lo_80'])} – {_fmt(pp['hi_80'])}", pp["verdict"])
                    for pp in entry["projections"]],
                   header=("FY", "Projected", "ETS 80% band", "Verdict"))
    else:
        doc.add_paragraph(f"Not available: {scr['error']}")

    # 7 ── Committee narrative
    _heading(doc, "7. Credit Committee Narrative (AI-drafted)")
    narr = _latest_narrative(name, db)
    if narr:
        doc.add_paragraph(
            f"Drafted by {narr['model']} on {narr['run_ts']} — full agent "
            f"attribution in the narrative audit table.")
        for para in narr["output_text"].split("\n\n"):
            if para.strip():
                doc.add_paragraph(para.strip())
    else:
        doc.add_paragraph(
            "No committee narrative on record — run the 4-agent committee "
            "(POST /narrative) and re-export.")

    doc.add_paragraph()
    doc.add_paragraph(DISCLAIMER).runs[0].bold = True

    doc.save(out_path)
    return out_path
