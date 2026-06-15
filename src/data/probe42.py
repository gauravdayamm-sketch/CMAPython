"""
Probe42 connector — official corporate-registry data over an authenticated
API (no scraping, no captchas). Pulls MCA master data, charges, directors
and (plan-permitting) compliance signals, caches every response locally to
conserve the per-call quota, and records the findings into the registry
due-diligence framework so the RFA logic and committee queries react
automatically.

Setup: put your key in .env (git-ignored, never committed) — see .env.example:

    PROBE42_API_KEY=xxxxxxxxxxxxxxxx
    # optional overrides:
    # PROBE42_BASE=https://api.probe42.in/probe_pro
    # PROBE42_VERSION=1.3
"""
import datetime
import json
import os
import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"
ENV  = ROOT / ".env"

CIN_RE   = re.compile(r"^[ULul]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}$")
LLPIN_RE = re.compile(r"^[A-Za-z]{3}-?\d{4}$")

CACHE_DAYS = 30


def _env(key, default=None):
    if os.environ.get(key):
        return os.environ[key]
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return default


def _client():
    import httpx
    key = _env("PROBE42_API_KEY")
    if not key:
        raise RuntimeError(
            "PROBE42_API_KEY not set. Add it to .env "
            "(see .env.example — the file is git-ignored).")
    base = _env("PROBE42_BASE", "https://api.probe42.in/probe_pro")
    return httpx.Client(
        base_url=base, timeout=30,
        headers={"x-api-key": key,
                 "x-api-version": _env("PROBE42_VERSION", "1.3"),
                 "accept": "application/json"},
    ), base


def search_entity(name, limit=8):
    """Search companies/LLPs by name. Returns candidate list."""
    client, base = _client()
    with client:
        resp = client.get(
            "/entities/searches",
            params={"nameStartsWith": name, "limit": limit})
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}: {resp.text[:300]}"
        data = resp.json()
    out = []
    for kind in ("companies", "llps", "entities"):
        for e in (data.get("data", data) or {}).get(kind, []) or []:
            out.append({
                "id": e.get("cin") or e.get("llpin") or e.get("id"),
                "name": e.get("legalName") or e.get("name"),
                "status": e.get("status"),
                "kind": kind.rstrip("s"),
            })
    return out, ""


def fetch_details(entity_id, force=False):
    """Comprehensive details for a CIN/LLPIN, cached for CACHE_DAYS."""
    entity_id = entity_id.strip().upper()
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM probe_cache WHERE entity_id=?",
            (entity_id,)).fetchone()
    if row and not force:
        age = (datetime.datetime.now()
               - datetime.datetime.fromisoformat(row[1])).days
        if age <= CACHE_DAYS:
            return json.loads(row[0]), ""

    kind = "llps" if LLPIN_RE.match(entity_id.replace("-", "")) else "companies"
    client, base = _client()
    with client:
        resp = client.get(f"/{kind}/{entity_id}/comprehensive-details")
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
        payload = resp.json()
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO probe_cache (entity_id, payload, fetched_at)
            VALUES (?, ?, ?)
        """, (entity_id, json.dumps(payload),
              datetime.datetime.now().isoformat()))
    return payload, ""


def summarize(payload):
    """Defensive extraction of the credit-relevant facts."""
    d = payload.get("data", payload) or {}
    comp = d.get("company") or d.get("llp") or d
    charges = d.get("charges") or comp.get("charges") or []
    open_charges = [c for c in charges
                    if str(c.get("status", "")).lower() not in
                    ("satisfied", "closed")]
    directors = (d.get("directors") or d.get("authorizedSignatories")
                 or comp.get("directors") or [])
    legal = d.get("legalHistory") or d.get("legalCases") or []

    summary = {
        "name":   comp.get("legalName") or comp.get("name"),
        "status": comp.get("status"),
        "incorporated": comp.get("incorporationDate"),
        "paid_up_capital": comp.get("paidUpCapital"),
        "open_charges": [
            {"holder": c.get("holderName") or c.get("chargeHolderName"),
             "amount": c.get("amount"),
             "date": c.get("date") or c.get("creationDate")}
            for c in open_charges[:15]],
        "n_open_charges": len(open_charges),
        "n_directors": len(directors),
        "n_legal_cases": len(legal) if isinstance(legal, list) else None,
    }
    status_bad = str(summary["status"] or "").lower() not in (
        "active", "active compliant", "")
    summary["adverse"] = bool(status_bad)
    bits = [f"status={summary['status']}",
            f"open charges={summary['n_open_charges']}"]
    if summary["open_charges"]:
        holders = {c["holder"] for c in summary["open_charges"] if c["holder"]}
        bits.append("charge holders: " + ", ".join(sorted(holders)[:5]))
    if summary["n_legal_cases"]:
        bits.append(f"legal cases on record={summary['n_legal_cases']}")
    summary["remarks"] = "; ".join(bits)[:300]
    return summary


def run_probe_check(borrower_name, entity_id, force=False):
    """Fetch + summarise + record as the MCA registry check."""
    from data.registry_checks import record_check
    payload, err = fetch_details(entity_id, force=force)
    if err:
        return None, err
    summary = summarize(payload)
    record_check(
        borrower_name, "mca",
        "adverse" if summary["adverse"] else "clear",
        remarks=f"[probe42] {summary['remarks']}",
        reference=entity_id,
    )
    return summary, ""


# ── Probe42 downloaded-report (PDF) importer ─────────────────────────────────
# No API key needed — the officer downloads the report from the portal and
# points the tool at it. Parsed against the real Probe42 report layout.

def _pdf_text(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def _flat(text):
    return " ".join(text.split())


def _negative(text, *phrases):
    """True when the report states the (good-news) negative is present."""
    low = text.lower()
    return any(p.lower() in low for p in phrases)


def parse_report_pdf(path):
    """Extract the credit-relevant facts from a Probe42 company report PDF."""
    return parse_report_text(_pdf_text(path))


def parse_report_text(raw):
    """Parse already-extracted report text (testable without a PDF)."""
    t = _flat(raw)

    def grab(pattern, group=1, flags=0):
        m = re.search(pattern, t, flags)
        return m.group(group).strip() if m else None

    cin = grab(r"CIN\s+([ULul]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6})")
    name = grab(r"Probe42\.in\s+([A-Z][A-Z0-9 &().,'-]+?)\s+Printed at")
    status = grab(r"Company Status\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+"
                  r"(?:Date of Last AGM|LEI|About)")
    incorporated = grab(r"Date of Incorporation\s+(\d{1,2} \w+, \d{4})")
    paid_up = grab(r"Paid Up Capital\s+Rs\.\s+([\d.]+)\s+Crore")
    sum_charges = grab(r"Sum of Charges\s+Rs\.\s+([\d.]+)\s+Crore")
    pan = grab(r"PAN\s+([A-Z]{5}\d{4}[A-Z])")

    # Open charges: holder + amount pairs in the Open Charges Sequence block.
    # Anchor on the table header ("Holder Name ... Property Type") so the
    # table-of-contents reference is skipped.
    charges = []
    block = re.search(
        r"Open Charges Sequence\s+Sl\. No\..*?Property Type(?:\s+No\. of"
        r" Holders)?(.*?)(?:Satisfied Charges|Open Charges Latest Events|"
        r"Peer Comparison|Auditors)", t)
    if block:
        for m in re.finditer(
                r"(?:Creation|Modification)\s+\d{1,2} \w+, \d{4}\s+"
                r"\d{1,2} \w+, \d{4}\s+([A-Z][A-Za-z0-9 &.,'()-]+?)\s+"
                r"([\d]+\.[\d]+)\s+(?:Immovable|Movable|Book|Floating|Others)",
                block.group(1)):
            charges.append({"holder": m.group(1).strip(),
                            "amount": float(m.group(2))})

    # Auditor qualifications (adverse if any "Yes")
    aud = re.search(r"Whether Auditors'? Report has been Qualified.*?"
                    r"Comments Given By(.*?)(?:Comments under|Page )", t)
    auditor_qualified = bool(aud and re.search(r"\b\d{4}\s+Yes\b", aud.group(1)))

    legal_against = not _negative(
        t, "There are no Cases Filed against this corporate")
    legal_dispute = not _negative(
        t, "there are no legal cases of financial dispute")
    bifr   = not _negative(t, "no BIFR Cases")
    cdr    = not _negative(t, "no CDR History")
    suit   = not _negative(t, "does not appear to have any Suit Filed Cases")
    removed = not _negative(t, "name was never removed under section 248")
    msme_delay = not _negative(
        t, "does not have any filings related to supplier")

    holders = sorted({c["holder"] for c in charges})
    adverse_reasons = []
    if status and status.lower() not in ("active", "active compliant"):
        adverse_reasons.append(f"company status = {status}")
    if auditor_qualified:
        adverse_reasons.append("auditor report qualified/adverse")
    if legal_against:
        adverse_reasons.append("cases filed against the corporate")
    if legal_dispute:
        adverse_reasons.append("legal cases of financial dispute on record")
    if bifr:
        adverse_reasons.append("BIFR history")
    if cdr:
        adverse_reasons.append("CDR history")
    if suit:
        adverse_reasons.append("suit-filed cases with bureaus")
    if removed:
        adverse_reasons.append("name removed u/s 248(5)")
    if msme_delay:
        adverse_reasons.append("MSME supplier payment delays filed")

    other_lenders = [h for h in holders if "STATE BANK OF INDIA" not in h.upper()]
    remarks_bits = [f"status={status}"]
    if holders:
        remarks_bits.append(f"open charges: {', '.join(holders[:6])}")
    if other_lenders:
        remarks_bits.append(f"OTHER LENDERS: {', '.join(other_lenders[:6])}")
    if adverse_reasons:
        remarks_bits.append("ADVERSE: " + "; ".join(adverse_reasons))

    return {
        "name": name, "cin": cin, "pan": pan, "status": status,
        "incorporated": incorporated, "paid_up_capital": paid_up,
        "sum_of_charges": sum_charges,
        "open_charges": charges, "n_open_charges": len(charges),
        "charge_holders": holders, "other_lenders": other_lenders,
        "auditor_qualified": auditor_qualified,
        "cases_against": legal_against, "financial_dispute": legal_dispute,
        "bifr": bifr, "cdr": cdr, "suit_filed": suit, "name_removed": removed,
        "msme_delays": msme_delay,
        "adverse": bool(adverse_reasons),
        "adverse_reasons": adverse_reasons,
        "remarks": _flat("; ".join(remarks_bits))[:400],
    }


def import_report(borrower_name, pdf_path):
    """Parse a downloaded Probe42 report and record the MCA registry check."""
    from data.registry_checks import record_check
    summary = parse_report_pdf(pdf_path)
    record_check(
        borrower_name, "mca",
        "adverse" if summary["adverse"] else "clear",
        remarks=f"[probe42 report] {summary['remarks']}",
        reference=summary.get("cin") or pathlib.Path(pdf_path).stem,
    )
    return summary
