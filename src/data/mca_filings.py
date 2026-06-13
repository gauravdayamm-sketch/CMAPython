"""
MCA filing intelligence — two documents the officer downloads from Probe42:

  1. Form AOC-4 (native digital e-Form): the financials AS FILED with MCA.
     Parsed exactly from the text layer and tied out against the borrower's
     CMA audited column. A material mismatch on a core figure is EWS #18 —
     concealment of material facts — Critical, which flips RFA.

  2. The audited annual report (a SCANNED PDF): OCR is reliable for prose
     but mangles digits, so this is read for NARRATIVE ONLY — the auditor's
     opinion (qualified / adverse / emphasis of matter) and CARO clause on
     statutory-dues arrears. Figures are never taken from a scan; the
     finding is surfaced as text with an explicit "verify against source"
     caveat. An adverse/qualified opinion is EWS #38.

Everything runs locally: pypdf for native text, Tesseract for OCR.
"""
import datetime
import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"

TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Material-difference gate for the tie-out: flag when BOTH the relative gap
# exceeds 10% AND the absolute gap exceeds ₹0.10 Cr (ignores rounding noise).
TIE_REL = 0.10
TIE_ABS = 0.10


# ── AOC-4 (native digital) ────────────────────────────────────────────────────

def _pdf_text(path):
    from pypdf import PdfReader
    return " ".join(
        " ".join((p.extract_text() or "").split())
        for p in PdfReader(path).pages)


def _first_num(text, label):
    """First absolute-rupee figure (≥4 digits) after a label → ₹ Crore.
    Allows roman-numeral/parenthetical clutter (e.g. '(VII-VIII)') between
    the label and the value."""
    m = re.search(re.escape(label) + r"[^\d]{0,40}(\d{4,})", text)
    if not m:
        return None
    return round(int(m.group(1)) / 1e7, 4)


def parse_aoc4(path):
    """Extract filed figures (₹ Cr) from a native AOC-4 e-Form PDF."""
    return parse_aoc4_text(_pdf_text(path))


def parse_aoc4_text(t):
    cin = (re.search(r"([ULul]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6})", t) or [None])
    cin = cin.group(1) if hasattr(cin, "group") else None
    fy_to = re.search(r"financial statements relates.*?To \(DD/MM/YYYY\)\s*"
                      r"(\d{2}/\d{2}/\d{4})", t)
    nature = re.search(r"Nature of financial statements[^A-Za-z]*"
                       r"([A-Za-z][A-Za-z /\-]+?financial statements|"
                       r"Adopted Financial statements)", t)

    fy_to_iso = None
    if fy_to:
        d, m, y = fy_to.group(1).split("/")
        fy_to_iso = f"{y}-{m}-{d}"

    figs = {
        "total_income":        _first_num(t, "Total Income (I+II)"),
        "revenue_operations":  _first_num(t, "Sale of goods manufactured"),
        "pbt":                 _first_num(t, "(IX) Profit before tax"),
        "pat":                 _first_num(t, "for the period from continuing operations"),
        "net_worth":           _first_num(t, "Net Worth of the company"),
        "trade_receivables":   _first_num(t, "Trade receivables"),
        "long_term_borrowings": _first_num(t, "borrowings"),
        "authorised_capital":  _first_num(t, "Authorised capital of the company"),
    }
    return {
        "cin": cin, "fy_to": fy_to_iso,
        "nature": nature.group(1).strip() if nature else None,
        "figures": figs,
    }


# CMA audited line/metric ↔ AOC-4 figure. Core figures (core=True) trigger
# EWS #18 on mismatch; others are verification items only.
TIE_MAP = [
    ("Net sales / Total income", "os_net_sales", "total_income", True),
    ("Profit before tax",        "os_pbt",       "pbt",          True),
    ("Profit after tax",         "os_pat",       "pat",          True),
    ("Tangible net worth",       "bs_tnw",       "net_worth",    True),
    ("Trade receivables",        None,           "trade_receivables", False),
]

# AOC-4 "Total Income" includes other income; the CMA net-sales line excludes
# it, so allow that one a wider gate before calling it a mismatch.


def tie_out_aoc4(borrower_name, filing, db_path=None):
    """Compare filed AOC-4 figures against the matching CMA audited year."""
    from analytics.wc_assessment import _load, compute_year_metrics
    borrower, years, _, _ = _load(borrower_name, db_path or DB)
    if not borrower:
        return {"error": f"Borrower not found: {borrower_name}", "rows": []}

    audited = [y for y in years
               if y["statement_type"] == "audited" and not y["is_dual"]]
    target = next((y for y in audited if y["fy_end"] == filing["fy_to"]),
                  audited[-1] if audited else None)
    if not target:
        return {"error": "No audited CMA year to tie out against", "rows": []}

    L = target["lines"]
    rmap = {"trade_receivables":
            L.get("bs_receivables_domestic", 0) + L.get("bs_receivables_export", 0)}

    rows, breaches = [], []
    for label, code, fig_key, core in TIE_MAP:
        filed = filing["figures"].get(fig_key)
        cma = rmap.get(fig_key) if code is None else L.get(code)
        if filed is None or cma is None:
            continue
        gap = filed - cma
        rel = abs(gap) / max(abs(cma), 0.01)
        material = rel > TIE_REL and abs(gap) > TIE_ABS
        rows.append({"item": label, "cma": round(cma, 2),
                     "filed": round(filed, 2), "diff": round(gap, 2),
                     "diff_pct": round(rel, 3),
                     "status": "MISMATCH" if material else "ok",
                     "core": core})
        if material and core:
            breaches.append(f"{label}: CMA {cma:.2f} vs filed {filed:.2f} Cr "
                            f"({gap:+.2f})")
    return {"error": "", "fy": target["fy_label"],
            "rows": rows, "breaches": breaches}


# ── Audit report (scanned) — narrative only ──────────────────────────────────

def _ocr_text(path, max_pages=14):
    import pypdfium2 as pdfium
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = TESSERACT
    pdf = pdfium.PdfDocument(path)
    out = []
    for i in range(min(max_pages, len(pdf))):
        img = pdf[i].render(scale=300 / 72).to_pil()
        out.append(pytesseract.image_to_string(img))
    return "\n".join(out)


# Opinion phrases — order matters (worst first wins). These are
# unambiguous enough to survive OCR; the unmodified phrases are the
# expected clean case.
OPINION_RULES = [
    ("ADVERSE",    ("adverse opinion",)),
    ("DISCLAIMER", ("disclaimer of opinion", "we do not express an opinion")),
    ("QUALIFIED",  ("qualified opinion", "basis for qualified opinion")),
    ("UNMODIFIED", ("true and fair", "unmodified opinion")),
]

# Arrears phrases chosen so they do NOT occur in clean CARO boilerplate.
# Clean reports say "regular in depositing" / "no undisputed dues in
# arrears"; only genuine arrears use "not been regular", "not deposited",
# "defaulted", "irregular in". The ambiguous bare "in arrears" is excluded
# precisely because clean reports use "no ... in arrears".
ARREARS_NEAR = ("not been regular", "not regular in depositing",
                "have not deposited", "not been deposited", "defaulted in",
                "irregular in depositing", "are in arrears of")


def _near(low, keyword, phrases, window=220):
    i = low.find(keyword)
    if i < 0:
        return False
    seg = low[max(0, i - window): i + window]
    return any(p in seg for p in phrases)


def analyse_audit_report(path):
    """OCR a scanned audit report for NARRATIVE signals (never figures).

    Conservative by design: OCR + ubiquitous boilerplate make false
    positives easy, so a finding is raised only when a negative phrase is
    present AND no clean phrasing sits beside it. Everything is a
    verification PROMPT — see `auto_findings` for the (narrow) subset
    confident enough to feed EWS."""
    return analyse_report_text(_ocr_text(path))


def analyse_report_text(text):
    """Narrative analysis of already-extracted text (OCR-free test seam)."""
    low = " ".join(text.split()).lower()

    opinion = "NOT DETECTED"
    for verdict, phrases in OPINION_RULES:
        if any(p in low for p in phrases):
            opinion = verdict
            break

    # CARO statutory dues — only when an unambiguous arrears phrase sits
    # near the keyword (these phrases never appear in clean boilerplate).
    statutory_arrears = any(
        _near(low, kw, ARREARS_NEAR)
        for kw in ("statutory dues", "statutory", "provident fund"))

    # NB: going-concern is deliberately NOT keyword-detected. The SA 570
    # standard embeds "material uncertainty" + "going concern" in every
    # audit report's responsibility paragraph, so OCR keyword matching
    # produces a false prompt on clean reports. It stays a manual read.

    prompts, auto = [], []
    if opinion in ("ADVERSE", "DISCLAIMER", "QUALIFIED"):
        prompts.append(f"Auditor opinion appears {opinion} — read the basis paragraph")
        auto.append((38, "High", f"Auditor opinion {opinion} (per scanned report)"))
    elif opinion == "UNMODIFIED":
        prompts.append("Auditor opinion appears clean (unmodified / true and fair)")
    else:
        prompts.append("Could not read the opinion clearly — check the signed report")
    if statutory_arrears:
        prompts.append("CARO may note statutory-dues arrears — verify clause (vii)")

    return {"opinion": opinion, "statutory_arrears": statutory_arrears,
            "flags": prompts,
            "auto_findings": auto, "chars": len(text),
            "caveat": "OCR of a scanned document — these are PROMPTS to verify "
                      "against the signed report, not confirmed findings. "
                      "Figures are never read from a scan."}


# ── Persistence + EWS feed ────────────────────────────────────────────────────

def _bid(conn, borrower_name):
    row = conn.execute("SELECT borrower_id FROM borrower WHERE name=?",
                       (borrower_name,)).fetchone()
    if not row:
        raise ValueError(f"Borrower not found: {borrower_name}")
    return row[0]


def _clear(borrower_name, kind, db_path):
    """Retract any prior finding of this kind — so a corrected re-import
    (e.g. a clean run after an earlier false positive) wipes the stale row."""
    with sqlite3.connect(db_path or DB) as conn:
        conn.execute("DELETE FROM filing_finding WHERE kind=? AND borrower_id="
                     "(SELECT borrower_id FROM borrower WHERE name=?)",
                     (kind, borrower_name))


def _record(borrower_name, kind, ews_id, severity, detail, source, db_path):
    with sqlite3.connect(db_path or DB) as conn:
        bid = _bid(conn, borrower_name)
        conn.execute("""
            INSERT OR REPLACE INTO filing_finding
            (borrower_id, kind, ews_id, severity, detail, source_file, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (bid, kind, ews_id, severity, detail, source,
              datetime.datetime.now().isoformat()))


def import_filing(path, borrower_name, db_path=None):
    """Dispatch by document type; record findings; return a summary dict."""
    path = str(path)
    text = _pdf_text(path)
    is_aoc4 = len(text) > 600 and bool(re.search(r"Form\s*No\.?\s*AOC-4", text[:600]))

    if is_aoc4:
        _clear(borrower_name, "tie_out", db_path)   # retract any prior tie-out
        filing = parse_aoc4_text(text)
        result = tie_out_aoc4(borrower_name, filing, db_path=db_path)
        result["kind"] = "aoc4"
        result["filing"] = filing
        if result.get("breaches"):
            _record(borrower_name, "tie_out", 18, "Critical",
                    "AOC-4 vs CMA mismatch — " + "; ".join(result["breaches"]),
                    pathlib.Path(path).name, db_path)
            result["recorded"] = "EWS #18 (concealment) — Critical"
        return result

    # Scanned → narrative. Only an unambiguous adverse/qualified OPINION is
    # confident enough off OCR to feed EWS; statutory-dues / going-concern
    # stay verification prompts (OCR + boilerplate make them unreliable).
    _clear(borrower_name, "auditor_opinion", db_path)  # retract any prior opinion
    rep = analyse_audit_report(path)
    rep["kind"] = "audit_report"
    if rep["auto_findings"]:
        ews_id, severity, detail = rep["auto_findings"][0]
        _record(borrower_name, "auditor_opinion", ews_id, severity,
                detail, pathlib.Path(path).name, db_path)
        rep["recorded"] = f"EWS #{ews_id} ({severity}) — verify against signed report"
    return rep


def filing_findings(borrower_id, db_path=None):
    """Recorded filing findings for the EWS engine."""
    with sqlite3.connect(db_path or DB) as conn:
        rows = conn.execute("""
            SELECT kind, ews_id, severity, detail FROM filing_finding
            WHERE borrower_id = ?
        """, (borrower_id,)).fetchall()
    return [{"kind": r[0], "ews_id": r[1], "severity": r[2], "detail": r[3]}
            for r in rows]
