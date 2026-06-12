"""
Probe42 connector — official corporate-registry data over an authenticated
API (no scraping, no captchas). Pulls MCA master data, charges, directors
and (plan-permitting) compliance signals, caches every response locally to
conserve the per-call quota, and records the findings into the registry
due-diligence framework so the RFA logic and committee queries react
automatically.

Setup: put your key in D:\\CMA_Python\\.env (git-ignored, never committed):

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
            "PROBE42_API_KEY not set. Add it to D:\\CMA_Python\\.env "
            "(the file is git-ignored).")
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
