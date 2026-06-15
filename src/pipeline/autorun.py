"""
One-shot pipeline: watched folders → credit memo, with live progress.

Folder layout (under <workspace>, default ./workspace):
    cma/          LLMS exports (BS/OS/Performance .xls/.csv) or a .xlsx workbook
    probe42/      Probe42 company report / Form AOC-4 / audited annual report PDFs
    notes/        the analyst's proposal note (.docx)  — optional
    loan_manual/  the loan manual (.md/.txt) — indexed once, kept stable
    output/       the finished credit memorandum lands here

The launcher ingests CMA data (which names the borrower), attaches every
filing/registry/note finding to that borrower, runs the full assessment +
committee, and writes the memo — printing each step as it happens.
"""
import datetime
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _say(step, total, label, detail="", flush=True):
    dots = "." * max(1, 26 - len(label))
    line = f"[{step}/{total}] {label} {dots} {detail}"
    print(line, flush=flush)


def _files(folder, exts):
    if not folder.exists():
        return []
    return sorted(f for f in folder.iterdir()
                  if f.is_file() and f.suffix.lower() in exts
                  and not f.name.lower().startswith(("readme", "~$")))


def classify_pdf(path):
    """probe42_report | aoc4 | audit_report(scanned) | unknown."""
    from data.mca_filings import _pdf_text
    head = ""
    try:
        head = _pdf_text(str(path))[:1500]
    except Exception:
        pass
    low = head.lower()
    if "probe42" in low:
        return "probe42_report"
    if "aoc-4" in low or "aoc4" in low or "filing financial statement" in low:
        return "aoc4"
    if len(head) < 300:
        return "audit_report"          # little/no text layer → scanned
    if "auditor" in low or "independent auditor" in low:
        return "audit_report"
    return "unknown"


def _existing_borrower(borrower, db_path):
    """A borrower already in the DB to fall back on when cma\\ is empty:
    the named override if it has CMA data, else the sole ingested one."""
    import sqlite3
    from data.loan_manual import DB as _DB
    with sqlite3.connect(db_path or _DB) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT b.name FROM borrower b "
            "JOIN cma_statement s ON s.borrower_id = b.borrower_id")]
    if borrower and borrower in names:
        return borrower
    if borrower:
        hit = [n for n in names if borrower.lower() in n.lower()]
        if len(hit) == 1:
            return hit[0]
    return names[0] if len(names) == 1 else None


def run_pipeline(workspace=None, borrower=None, with_committee=True,
                 db_path=None):
    """Execute the whole pipeline. Returns the memo path (or None)."""
    ws = pathlib.Path(workspace or (ROOT / "workspace"))
    cma_dir   = ws / "cma"
    probe_dir = ws / "probe42"
    notes_dir = ws / "notes"
    manual_dir = ws / "loan_manual"
    out_dir   = ws / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    TOTAL = 8
    t0 = time.time()
    print("=" * 64)
    print(f"CMA AUTO-RUN  ·  {datetime.datetime.now():%d-%b-%Y %H:%M}")
    print(f"Workspace: {ws}")
    print("=" * 64)

    # 1 ── Loan manual (index once; re-index only if changed) ─────────────────
    from data.loan_manual import index_manual, manual_indexed
    manual_files = _files(manual_dir, {".md", ".txt"})
    if manual_files:
        n = manual_indexed(db_path=db_path)
        if n == 0:
            cnt = index_manual(str(manual_files[0]), db_path=db_path)
            _say(1, TOTAL, "Loan manual", f"indexed {cnt} passages")
        else:
            _say(1, TOTAL, "Loan manual", f"already indexed ({n} passages)")
    else:
        _say(1, TOTAL, "Loan manual", "none in loan_manual\\ — queries ungrounded")

    # 2 ── CMA ingestion (defines the borrower) ───────────────────────────────
    from ingest.cma_workbook import ingest_workbook
    from ingest.llms_export import find_borrower_sets, ingest_llms_set

    name = borrower
    sets = find_borrower_sets(cma_dir)
    workbooks = _files(cma_dir, {".xlsx", ".xlsm"})
    if sets:
        prefix, fileset = next(iter(sets.items()))
        data, bid = ingest_llms_set(fileset["bs"], fileset["os"],
                                    perf_path=fileset.get("perf"),
                                    db_path=db_path)
        if data.errors():
            print("  ERROR ingesting CMA exports:")
            for e in data.errors():
                print(f"    - {e}")
            return None
        name = data.borrower["name"]
        _say(2, TOTAL, "CMA data", f"{name} ({len(data.years)} year-cols, LLMS)")
    elif workbooks:
        data, bid = ingest_workbook(workbooks[0], db_path=db_path)
        if data.errors():
            print("  ERROR ingesting workbook:")
            for e in data.errors():
                print(f"    - {e}")
            return None
        name = data.borrower["name"]
        _say(2, TOTAL, "CMA data", f"{name} ({len(data.years)} year-cols, workbook)")
    else:
        # No fresh CMA files — fall back to a borrower already in the database
        # (e.g. re-running to add a later-arriving AOC-4). Use the override,
        # else the single ingested borrower if unambiguous.
        name = _existing_borrower(borrower, db_path)
        if not name:
            print(f"  ERROR: no CMA files in {cma_dir}\\ and no borrower to "
                  "fall back on. Drop the LLMS exports (Balance_Sheet/"
                  "Operating_Statement/Performance) or a .xlsx there, or pass "
                  "-b \"NAME\" for a borrower already ingested.")
            return None
        _say(2, TOTAL, "CMA data", f"{name} (already ingested — no new files)")

    # 3 ── Probe42 reports & MCA filings ──────────────────────────────────────
    from data import probe42
    from data.mca_filings import import_filing
    pdfs = _files(probe_dir, {".pdf"})
    notes_out = []
    for pdf in pdfs:
        kind = classify_pdf(pdf)
        try:
            if kind == "probe42_report":
                s = probe42.import_report(name, str(pdf))
                notes_out.append(f"report→{'ADVERSE' if s['adverse'] else 'clear'}")
            elif kind in ("aoc4", "audit_report"):
                r = import_filing(str(pdf), name, db_path=db_path)
                if r.get("kind") == "aoc4":
                    notes_out.append("AOC-4→" +
                                     ("MISMATCH" if r.get("breaches") else "tie-out OK"))
                else:
                    notes_out.append(f"audit→{r.get('opinion', '?').lower()}")
            else:
                notes_out.append(f"{pdf.name}: unrecognised, skipped")
        except Exception as e:
            notes_out.append(f"{pdf.name}: {type(e).__name__}")
    _say(3, TOTAL, "Probe42 / MCA filings",
         f"{len(pdfs)} doc(s): {'; '.join(notes_out) if notes_out else 'none'}")

    # 4 ── Proposal note (optional, local-only) ───────────────────────────────
    note_files = _files(notes_dir, {".docx"})
    if note_files:
        try:
            from report.note_check import check_note
            nc = check_note(str(note_files[0]), borrower_name=name, db_path=db_path)
            _say(4, TOTAL, "Proposal note",
                 f"{nc['traced']}/{nc['total']} figures traced, "
                 f"{len(nc['untraced'])} to verify")
        except Exception as e:
            _say(4, TOTAL, "Proposal note", f"skipped ({type(e).__name__})")
    else:
        _say(4, TOTAL, "Proposal note", "none — qualitative review offline")

    # 5 ── Quantitative analysis ──────────────────────────────────────────────
    from analytics.wc_assessment import assess_working_capital
    from analytics.ews import run_full_ews
    a = assess_working_capital(name, db_path=db_path)
    basis_year, basis = a.assessment_basis
    ews = run_full_ews(name, db_path=db_path)
    _say(5, TOTAL, "Assessment",
         f"basis {basis.split(' — ')[0]} {basis_year['fy_label'] if basis_year else '?'}; "
         f"{len(ews.triggered_red_flags)+len(ews.triggered_exceptions)} flags, "
         f"RFA {'YES' if ews.rfa_required else 'no'}")

    # 6 ── Committee narrative (Ollama) ───────────────────────────────────────
    if with_committee:
        try:
            from agents.committee import run_committee
            print("[6/8] Committee narrative .... running 4 agents "
                  "(Ollama, 60-120s)...", flush=True)
            result = run_committee(borrower_name=name)
            if result.error:
                _say(6, TOTAL, "Committee narrative", f"unavailable ({result.error})")
            else:
                _say(6, TOTAL, "Committee narrative",
                     f"{result.verdict} (confidence {result.confidence})")
        except Exception as e:
            _say(6, TOTAL, "Committee narrative", f"skipped ({type(e).__name__})")
    else:
        _say(6, TOTAL, "Committee narrative", "skipped (--no-committee)")

    # 7 ── Benchmark refresh (book peers) — implicit in memo ───────────────────
    _say(7, TOTAL, "Peer benchmark", "positioned against the book")

    # 8 ── Memo ───────────────────────────────────────────────────────────────
    from report.memo_docx import generate_credit_memo
    safe = "".join(c if c.isalnum() else "_" for c in name)[:60]
    memo_path = out_dir / f"Credit_Memo_{safe}.docx"
    try:
        written = generate_credit_memo(name, out_path=memo_path, db_path=db_path)
    except Exception as e:
        print(f"  ERROR generating memo: {e}")
        return None
    _say(8, TOTAL, "Credit memorandum", str(written))

    print("=" * 64)
    print(f"DONE in {time.time()-t0:.0f}s  →  {written}")
    print("=" * 64)
    return written
