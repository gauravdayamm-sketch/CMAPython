"""Tests for the Probe42 downloaded-report parser (no PDF, no network).

Text fixtures mirror the real Probe42 report layout (validated against an
actual SBI-marked report for Arrowin Metaltech).
"""
import sqlite3
import pytest

from data.probe42 import parse_report_text, import_report
from data.registry_checks import get_checks
from analytics.ews import run_full_ews
from ingest.demo_fixture import BORROWER

CLEAN = (
    "Probe42.in ACME WIDGETS PRIVATE LIMITED Printed at : 12 Jun, 2026 "
    "Date of Incorporation 9 Nov, 2020 CIN U28999GJ2020PTC118078 "
    "PAN AAUCA3544Q Paid Up Capital Rs. 3.50 Crore Sum of Charges Rs. 3.37 "
    "Crore Company Status Active Date of Last AGM 31 May, 2025 "
    "Open Charges Sequence Sl. No. Charge ID Status Date Filing Date "
    "Holder Name Amount (Rs. Crore) Property Type No. of Holders "
    "Modification 17 Oct, 2024 10 Dec, 2024 STATE BANK OF INDIA 3.37 "
    "Immovable property 1 100530822 Creation 10 Jan, 2022 7 Feb, 2022 "
    "STATE BANK OF INDIA 2.50 Immovable property 1 Satisfied Charges "
    "Whether Auditors' Report has been Qualified Comments Given By "
    "2025 No 2024 No 2023 No Comments under "
    "There are no Cases Filed against this corporate "
    "there are no legal cases of financial dispute disposed "
    "no BIFR Cases no CDR History "
    "does not appear to have any Suit Filed Cases with the Bureaus "
    "name was never removed under section 248(5) "
    "does not have any filings related to supplier"
)

ADVERSE = (
    "Probe42.in STRESSED CORP PRIVATE LIMITED Printed at : 12 Jun, 2026 "
    "Date of Incorporation 1 Apr, 2015 CIN U17000MH2015PTC000001 "
    "Paid Up Capital Rs. 5.00 Crore Company Status Strike Off "
    "Date of Last AGM 30 Sep, 2023 "
    "Open Charges Sequence Sl. No. Charge ID Status Date Filing Date "
    "Holder Name Amount (Rs. Crore) Property Type No. of Holders "
    "Creation 5 May, 2021 1 Jun, 2021 HDFC BANK LIMITED 12.00 "
    "Movable property 1 Creation 2 Feb, 2020 1 Mar, 2020 "
    "BAJAJ FINANCE LIMITED 8.50 Book debts 1 Satisfied Charges "
    "Whether Auditors' Report has been Qualified Comments Given By "
    "2024 Yes 2023 No Comments under "
    "Cases Filed Against this Corporate 3 cases "
    "no BIFR Cases no CDR History "
    "does not appear to have any Suit Filed Cases with the Bureaus "
    "name was never removed under section 248 "
    "does not have any filings related to supplier"
)


def test_clean_report_parsed():
    s = parse_report_text(CLEAN)
    assert s["cin"] == "U28999GJ2020PTC118078"
    assert s["status"] == "Active"
    assert s["incorporated"] == "9 Nov, 2020"
    assert s["paid_up_capital"] == "3.50"
    assert s["n_open_charges"] == 2
    assert s["charge_holders"] == ["STATE BANK OF INDIA"]
    assert s["other_lenders"] == []          # SBI's own charges
    assert not s["adverse"]
    assert s["adverse_reasons"] == []


def test_adverse_report_flags_everything():
    s = parse_report_text(ADVERSE)
    assert s["status"] == "Strike Off"
    assert s["adverse"]
    assert "company status = Strike Off" in s["adverse_reasons"]
    assert "auditor report qualified/adverse" in s["adverse_reasons"]
    assert "cases filed against the corporate" in s["adverse_reasons"]
    # Other lenders surfaced — the "who else has lent" signal
    assert set(s["other_lenders"]) == {"HDFC BANK LIMITED",
                                       "BAJAJ FINANCE LIMITED"}


def test_import_records_clean_check(ingested, monkeypatch):
    data, bid, expected, db = ingested
    import data.registry_checks as rc
    monkeypatch.setattr(rc, "DB", db)
    monkeypatch.setattr("data.probe42.parse_report_pdf",
                        lambda p: parse_report_text(CLEAN))
    from data.probe42 import import_report as imp
    s = imp(BORROWER, "dummy.pdf")
    assert not s["adverse"]
    checks = {c["registry"]: c for c in get_checks(BORROWER, db_path=db)}
    assert checks["mca"]["status"] == "clear"
    assert "probe42" in checks["mca"]["remarks"]


def test_import_adverse_drives_ews(ingested, monkeypatch):
    data, bid, expected, db = ingested
    import data.registry_checks as rc
    monkeypatch.setattr(rc, "DB", db)
    monkeypatch.setattr("data.probe42.parse_report_pdf",
                        lambda p: parse_report_text(ADVERSE))
    from data.probe42 import import_report as imp
    imp(BORROWER, "dummy.pdf")
    ews = run_full_ews(BORROWER, db_path=db)
    hit = [i for i in ews.ews_indicators if i.get("source") == "registry:mca"]
    assert hit                                # adverse MCA → EWS indicator
