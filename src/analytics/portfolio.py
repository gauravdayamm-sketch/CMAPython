"""
Portfolio view — one row per borrower with ingested CMA data, summarising
the whole book: scale, liquidity, leverage, coverage, red flags, RFA
status, model consensus and adverse media.
"""
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"


def portfolio_summary(db_path=None):
    """List of per-borrower summary dicts, worst credits first."""
    from analytics.wc_assessment import assess_working_capital
    from analytics.ews import run_full_ews
    from analytics.scores import run_ensemble

    db = db_path or DB
    with sqlite3.connect(db) as conn:
        rows = conn.execute("""
            SELECT DISTINCT b.borrower_id, b.name FROM borrower b
            JOIN cma_statement s ON s.borrower_id = b.borrower_id
            ORDER BY b.name
        """).fetchall()

    book = []
    for bid, name in rows:
        a = assess_working_capital(name, db_path=db)
        if a.error:
            continue
        latest, basis_label = a.assessment_basis
        m = latest["metrics"] if latest else {}

        ews = run_full_ews(name, db_path=db)
        ens = run_ensemble(name, db_path=db)
        zones = [s.zone for s in (ens.ohlson, ens.altman, ens.zmijewski)
                 if not ens.error and s]

        with sqlite3.connect(db) as conn:
            adverse_news = conn.execute("""
                SELECT COUNT(*) FROM news_cache
                WHERE borrower_id = ? AND classification = 'ADVERSE'
            """, (bid,)).fetchone()[0]

        n_flags = (len(ews.triggered_red_flags)
                   + len(ews.triggered_exceptions)) if not ews.error else None
        book.append({
            "borrower":        name,
            "latest_audited":  latest["fy_label"] if latest else None,
            "basis":           basis_label.split(" — ")[0].lower(),
            "net_sales":       m.get("net_sales"),
            "current_ratio":   m.get("current_ratio"),
            "tol_tnw":         m.get("tol_tnw"),
            "avg_gross_dscr":  a.dscr_avg_gross,
            "red_flags":       n_flags,
            "red_flag_verdict": ews.red_flag_verdict if not ews.error else "n/a",
            "rfa_required":    ews.rfa_required if not ews.error else False,
            "distress_models": zones.count("DISTRESS"),
            "ensemble":        "/".join(z[0] for z in zones) if zones else "—",
            "adverse_news":    adverse_news,
        })

    book.sort(key=lambda r: (
        not r["rfa_required"],
        -(r["red_flags"] or 0),
        -(r["distress_models"] or 0),
    ))
    return book
