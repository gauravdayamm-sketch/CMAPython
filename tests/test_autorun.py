"""Tests for the autorun launcher and the finding-retraction fix."""
import sqlite3
import pytest

from data import mca_filings
from data.mca_filings import _record, _clear, filing_findings, import_filing
from pipeline.autorun import run_pipeline, classify_pdf
from ingest.demo_fixture import BORROWER


def _bid(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT borrower_id FROM borrower WHERE name=?",
                         (BORROWER,)).fetchone()[0]


def test_corrected_reimport_retracts_stale_finding(ingested, monkeypatch):
    """A clean re-run of a scanned report must wipe a prior false-positive
    finding — the bug that wrongly kept Arrowin red-flagged."""
    data, bid, expected, db = ingested
    # Simulate the earlier buggy run that recorded a Critical statutory-dues
    _record(BORROWER, "auditor_opinion", 1, "Critical",
            "Possible statutory-dues arrears noted in CARO", "old.pdf", db)
    assert filing_findings(_bid(db), db_path=db)

    # Corrected re-import: a CLEAN scanned report (no figures, OCR mocked)
    clean = ("Independent Auditor's Report. In our opinion the statements "
             "give a true and fair view. The company is regular in depositing "
             "undisputed statutory dues; there were no outstanding statutory "
             "dues as on March 31, 2025.")
    monkeypatch.setattr(mca_filings, "_pdf_text", lambda p: "")        # → scanned
    monkeypatch.setattr(mca_filings, "_ocr_text", lambda p, **k: clean)
    rep = import_filing("audit.pdf", BORROWER, db_path=db)

    assert rep["opinion"] == "UNMODIFIED"
    assert filing_findings(_bid(db), db_path=db) == []   # stale row retracted


def test_clear_is_scoped_to_kind(ingested):
    data, bid, expected, db = ingested
    _record(BORROWER, "tie_out", 18, "Critical", "mismatch", "a.pdf", db)
    _record(BORROWER, "auditor_opinion", 38, "High", "qualified", "b.pdf", db)
    _clear(BORROWER, "tie_out", db)
    kinds = {f["kind"] for f in filing_findings(_bid(db), db_path=db)}
    assert kinds == {"auditor_opinion"}                  # only tie_out cleared


def test_autorun_end_to_end(ingested, tmp_path):
    """Launcher runs against an already-ingested borrower (empty cma\\),
    no committee, and writes a memo."""
    data, bid, expected, db = ingested
    ws = tmp_path / "workspace"
    for sub in ("cma", "probe42", "notes", "loan_manual", "output"):
        (ws / sub).mkdir(parents=True)
    memo = run_pipeline(workspace=ws, borrower=BORROWER,
                        with_committee=False, db_path=db)
    assert memo is not None and memo.exists()
    assert memo.parent == ws / "output"


def test_autorun_no_cma_no_fallback(temp_db, tmp_path):
    """Empty cma\\ and no ingested borrower → clean failure, not a crash."""
    ws = tmp_path / "workspace"
    (ws / "cma").mkdir(parents=True)
    assert run_pipeline(workspace=ws, with_committee=False, db_path=temp_db) is None


def test_classify_pdf_unknown(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 not really")
    assert classify_pdf(f) in ("unknown", "audit_report")  # short text → scanned-ish
