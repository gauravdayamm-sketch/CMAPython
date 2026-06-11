"""
Public-registry due diligence framework.

Indian government portals (MCA, GST, IBBI, EPFO, e-courts) are mostly
captcha-walled, so automated pulls are not dependable. This module instead:

  * gives the desk officer DEEP LINKS to each portal search, pre-filled
    where the identifiers allow,
  * records what was found (clear / adverse / pending) with remarks,
  * treats a registry never checked — or checked > 90 days ago — as a
    due-diligence GAP that surfaces as a committee query,
  * maps an 'adverse' result onto the corresponding RBI EWS indicator so
    the RFA logic sees it automatically.
"""
import datetime
import pathlib
import sqlite3
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"

STALE_DAYS = 90

# registry → (display name, what to verify, EWS indicator id on adverse,
#             url builder taking (name, cif))
REGISTRIES = {
    "mca": (
        "MCA company master data",
        "CIN status, charges registered (other lenders!), director changes, "
        "filing arrears",
        29,
        lambda name, cif: "https://www.mca.gov.in/content/mca/global/en/"
                          "mca/master-data/MDS.html",
    ),
    "gst": (
        "GST portal — taxpayer search",
        "GSTIN active? GSTR-3B filing table — gaps mean statutory arrears",
        1,
        lambda name, cif: "https://services.gst.gov.in/services/searchtp",
    ),
    "ibbi": (
        "IBBI insolvency cases",
        "CIRP / liquidation proceedings against borrower or group",
        29,
        lambda name, cif: "https://ibbi.gov.in/en/orders/ibbi?title="
                          + urllib.parse.quote(name),
    ),
    "epfo": (
        "EPFO establishment search",
        "PF registration, payment defaults, employee-count trend",
        1,
        lambda name, cif: "https://unifiedportal-epfo.epfindia.gov.in/"
                          "publicPortal/no-auth/misReport/home/loadEstSearchHome",
    ),
    "ecourts": (
        "e-Courts party-name search",
        "Pending civil/criminal litigation by or against the borrower",
        30,
        lambda name, cif: "https://services.ecourts.gov.in/ecourtindia_v6/",
    ),
}


def _borrower(conn, borrower_name):
    return conn.execute(
        "SELECT borrower_id, name, cin FROM borrower WHERE name = ?",
        (borrower_name,)).fetchone()


def record_check(borrower_name, registry, status, remarks="", reference="",
                 db_path=None):
    """Record (or update) a registry check result."""
    if registry not in REGISTRIES:
        raise ValueError(f"Unknown registry '{registry}' "
                         f"(choose from {sorted(REGISTRIES)})")
    if status not in ("clear", "adverse", "pending"):
        raise ValueError("status must be clear / adverse / pending")
    with sqlite3.connect(db_path or DB) as conn:
        row = _borrower(conn, borrower_name)
        if not row:
            raise ValueError(f"Borrower not found: {borrower_name}")
        conn.execute("""
            INSERT OR REPLACE INTO registry_check
            (borrower_id, registry, checked_on, status, remarks, reference)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (row[0], registry, datetime.date.today().isoformat(),
              status, remarks, reference))


def get_checks(borrower_name, db_path=None):
    """All registries with their current check state for a borrower."""
    with sqlite3.connect(db_path or DB) as conn:
        conn.row_factory = sqlite3.Row
        row = _borrower(conn, borrower_name)
        if not row:
            return []
        existing = {r["registry"]: dict(r) for r in conn.execute(
            "SELECT * FROM registry_check WHERE borrower_id = ?", (row[0],))}

    today = datetime.date.today()
    out = []
    for key, (label, what, ews_id, url_fn) in REGISTRIES.items():
        rec = existing.get(key)
        if rec:
            age = (today - datetime.date.fromisoformat(rec["checked_on"])).days
            state = ("stale" if age > STALE_DAYS and rec["status"] != "adverse"
                     else rec["status"])
        else:
            age, state = None, "unchecked"
        out.append({
            "registry": key, "label": label, "verify": what,
            "url": url_fn(row[1], row[2]),
            "status": state, "checked_on": rec["checked_on"] if rec else None,
            "age_days": age, "remarks": rec["remarks"] if rec else "",
            "ews_indicator": ews_id,
        })
    return out


def due_diligence_gaps(borrower_name, db_path=None):
    """Registries unchecked or stale — feed for committee queries."""
    return [c for c in get_checks(borrower_name, db_path=db_path)
            if c["status"] in ("unchecked", "stale", "pending")]


def adverse_registries(borrower_id, db_path=None):
    """Adverse registry results → list of (registry, remarks, ews_indicator)."""
    with sqlite3.connect(db_path or DB) as conn:
        rows = conn.execute("""
            SELECT registry, remarks FROM registry_check
            WHERE borrower_id = ? AND status = 'adverse'
        """, (borrower_id,)).fetchall()
    out = []
    for registry, remarks in rows:
        if registry in REGISTRIES:
            out.append((registry, remarks, REGISTRIES[registry][2]))
    return out
