"""Committee queries must cite the loan manual when it is indexed."""
import sqlite3

from data.loan_manual import index_manual
from report.committee_queries import generate_queries
from ingest.demo_fixture import BORROWER
from tests.test_loan_manual import SAMPLE


def _trigger_cr_breach(db, bid):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            UPDATE cma_line SET value = value * 1.6
            WHERE line_code = 'bs_tcl' AND stmt_id =
                (SELECT stmt_id FROM cma_statement
                 WHERE borrower_id=? AND statement_type='audited'
                 AND is_dual=0 ORDER BY col_index DESC LIMIT 1)
        """, (bid,))


def test_queries_cite_manual_when_indexed(ingested, tmp_path):
    data, bid, expected, db = ingested
    f = tmp_path / "manual.md"
    f.write_text(SAMPLE, encoding="utf-8")
    index_manual(f, db_path=db)

    _trigger_cr_breach(db, bid)
    queries = generate_queries(BORROWER, db_path=db)

    cr = next((q for q in queries if q.get("rule_id") == "RF5"), None)
    assert cr is not None, [q["parameter"] for q in queries]
    assert cr["manual_ref"] is not None
    assert "Chapter 7" in cr["manual_ref"]["chapter"]
    assert "current" in cr["manual_ref"]["extract"].lower()

    # The tamper turns NWC negative — RF6 fires and is grounded on the
    # manual's net-working-capital passage
    rf6 = next(q for q in queries if q.get("rule_id") == "RF6")
    assert rf6["manual_ref"] is not None
    assert "Chapter 7" in rf6["manual_ref"]["chapter"]


def test_queries_survive_without_manual(ingested):
    data, bid, expected, db = ingested
    _trigger_cr_breach(db, bid)
    queries = generate_queries(BORROWER, db_path=db)
    assert queries
    assert all(q["manual_ref"] is None for q in queries)
