"""
Peer & industry benchmarking — two yardsticks, neither needing anything
the desk officer cannot provide:

  1. THE OWN BOOK: every borrower ever ingested becomes a peer. Metrics
     are computed uniformly from the canonical financials table, the
     subject is placed at a percentile among same-industry peers (whole
     book when the industry is thin), and the comparison sharpens with
     every proposal processed.
  2. INDUSTRY NORMS: a small table of sector medians the officer
     maintains quarterly from public rating-agency publications
     (CRISIL/ICRA sector notes), compared against the same metrics.
"""
import datetime
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"

MIN_INDUSTRY_PEERS = 3

# metric key, label, higher-is-better
METRICS = [
    ("ebitda_margin", "EBITDA margin",        True),
    ("pat_margin",    "PAT margin",           True),
    ("sales_growth",  "Sales growth YoY",     True),
    ("current_ratio", "Current ratio",        True),
    ("tol_tnw",       "TOL/TNW",              False),
    ("dso",           "DSO (days)",           False),
]


def _metrics_from_financials(rows):
    """Uniform metrics from a borrower's financials rows (oldest→latest)."""
    if not rows:
        return None
    f = rows[-1]
    sales = f["sales"] or 0
    if sales <= 0:
        return None
    tnw = f["tnw"] if f["tnw"] else (f["total_assets"] or 0) - (f["total_liabilities"] or 0)
    tol = f["tol"] if f["tol"] else (f["total_liabilities"] or 0)
    out = {
        "fy_end":        f["fy_end"],
        "net_sales":     sales,
        "ebitda_margin": (f["ebitda"] or 0) / sales,
        "pat_margin":    (f["net_income"] or 0) / sales,
        "current_ratio": ((f["current_assets"] or 0) / f["current_liabilities"])
                         if f["current_liabilities"] else None,
        "tol_tnw":       (tol / tnw) if tnw and tnw > 0 else None,
        "dso":           ((f["receivables"] or 0) * 365 / sales)
                         if f["receivables"] else None,
        "sales_growth":  None,
    }
    if len(rows) > 1 and (rows[-2]["sales"] or 0) > 0:
        out["sales_growth"] = (sales - rows[-2]["sales"]) / rows[-2]["sales"]
    return out


def _book(db_path):
    """{name: (industry, metrics)} for every borrower with usable data."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        borrowers = conn.execute(
            "SELECT borrower_id, name, industry FROM borrower").fetchall()
        book = {}
        for b in borrowers:
            rows = conn.execute("""
                SELECT * FROM financials WHERE borrower_id = ?
                ORDER BY fy_end ASC
            """, (b["borrower_id"],)).fetchall()
            m = _metrics_from_financials(rows)
            if m:
                book[b["name"]] = (b["industry"], m)
    return book


def _percentile(value, peers, higher_better):
    """Share of peers the subject beats (0–100)."""
    if value is None or not peers:
        return None
    better = sum(1 for p in peers
                 if (value > p) == higher_better or value == p)
    return round(100 * better / len(peers))


def set_industry(borrower_name, industry, db_path=None):
    with sqlite3.connect(db_path or DB) as conn:
        cur = conn.execute(
            "UPDATE borrower SET industry = ? WHERE name = ?",
            (industry, borrower_name))
        if cur.rowcount == 0:
            raise ValueError(f"Borrower not found: {borrower_name}")


def set_norms(industry, values, source="", db_path=None):
    """values: {metric_key: median}. Unknown keys rejected."""
    valid = {k for k, _, _ in METRICS}
    bad = set(values) - valid
    if bad:
        raise ValueError(f"Unknown metrics {sorted(bad)}; "
                         f"choose from {sorted(valid)}")
    today = datetime.date.today().isoformat()
    with sqlite3.connect(db_path or DB) as conn:
        for metric, median in values.items():
            conn.execute("""
                INSERT OR REPLACE INTO industry_norm
                (industry, metric, median, source, updated_on)
                VALUES (?, ?, ?, ?, ?)
            """, (industry, metric, float(median), source, today))


def get_norms(industry=None, db_path=None):
    with sqlite3.connect(db_path or DB) as conn:
        conn.row_factory = sqlite3.Row
        if industry:
            rows = conn.execute(
                "SELECT * FROM industry_norm WHERE industry = ?",
                (industry,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM industry_norm ORDER BY industry").fetchall()
    return [dict(r) for r in rows]


def benchmark(borrower_name, db_path=None):
    """
    Position one borrower against (a) the book and (b) industry norms.
    Returns {"borrower", "industry", "peer_set", "n_peers", "rows": [...]}.
    """
    db = db_path or DB
    book = _book(db)
    if borrower_name not in book:
        return {"borrower": borrower_name, "rows": [],
                "error": "Borrower not found or has no usable financials"}

    industry, subject = book[borrower_name]
    peers = {n: m for n, (ind, m) in book.items() if n != borrower_name}
    same_ind = {n: m for n, m in peers.items()
                if industry and book[n][0] == industry}
    if len(same_ind) >= MIN_INDUSTRY_PEERS:
        peer_set, peer_label = same_ind, f"industry peers ({industry})"
    else:
        peer_set, peer_label = peers, "whole book (industry set too thin)"

    norms = {r["metric"]: r for r in get_norms(industry, db_path=db)} \
        if industry else {}

    rows = []
    for key, label, higher_better in METRICS:
        value = subject.get(key)
        peer_vals = [m[key] for m in peer_set.values()
                     if m.get(key) is not None]
        median = (sorted(peer_vals)[len(peer_vals) // 2]
                  if peer_vals else None)
        norm = norms.get(key)
        rows.append({
            "metric": label, "key": key, "value": value,
            "peer_median": median,
            "percentile": _percentile(value, peer_vals, higher_better),
            "n_peers": len(peer_vals),
            "norm_median": norm["median"] if norm else None,
            "norm_source": norm["source"] if norm else None,
            "higher_better": higher_better,
        })

    weak = [r for r in rows
            if r["percentile"] is not None and r["n_peers"] >= 3
            and r["percentile"] <= 25]
    return {
        "borrower": borrower_name, "industry": industry or "not set",
        "peer_set": peer_label, "n_peers": len(peer_set),
        "rows": rows, "weak_metrics": [r["metric"] for r in weak],
        "error": "",
    }
