"""Tests for the committee query generator."""
import sqlite3
import pytest

from report.committee_queries import generate_queries
from ingest.demo_fixture import BORROWER


def test_clean_borrower_gets_only_structural_queries(ingested):
    """The demo borrower is financially clean: the only queries are the
    over-MPBF proposed limit and the unchecked-registry due-diligence gap."""
    data, bid, expected, db = ingested
    queries = generate_queries(BORROWER, db_path=db)
    params = [q["parameter"] for q in queries]
    assert "Limit assessment" in params
    limit_q = next(q for q in queries if q["parameter"] == "Limit assessment")
    assert limit_q["priority"] == 1
    assert "220" in limit_q["observation"]
    # No breach/red-flag questions on a clean borrower
    assert all(p in ("Limit assessment", "Due diligence") for p in params), params


def test_estimate_miss_generates_credibility_query(ingested):
    data, bid, expected, db = ingested
    with sqlite3.connect(db) as conn:
        conn.execute("""
            UPDATE cma_line SET value = 1300.0
            WHERE line_code = 'os_net_sales' AND stmt_id =
                (SELECT stmt_id FROM cma_statement
                 WHERE borrower_id=? AND is_dual=1)
        """, (bid,))
    queries = generate_queries(BORROWER, db_path=db)
    cred = [q for q in queries if q["parameter"] == "Estimate credibility"]
    assert cred and cred[0]["priority"] == 1
    assert "haircut" in cred[0]["question"]


def test_breach_generates_rule_question(ingested):
    data, bid, expected, db = ingested
    with sqlite3.connect(db) as conn:
        conn.execute("""
            UPDATE cma_line SET value = -25.0
            WHERE line_code = 'os_pat' AND stmt_id =
                (SELECT stmt_id FROM cma_statement
                 WHERE borrower_id=? AND statement_type='audited'
                 AND is_dual=0 ORDER BY col_index DESC LIMIT 1)
        """, (bid,))
    queries = generate_queries(BORROWER, db_path=db)
    profit = [q for q in queries if q["parameter"] == "Profitability"]
    assert profit
    assert "turnaround plan" in profit[0]["question"]


def test_aggressive_projection_generates_query(ingested):
    data, bid, expected, db = ingested
    with sqlite3.connect(db) as conn:
        conn.execute("""
            UPDATE cma_line SET value = value * 1.8
            WHERE line_code = 'os_net_sales' AND stmt_id =
                (SELECT stmt_id FROM cma_statement
                 WHERE borrower_id=? AND statement_type='projected'
                 ORDER BY col_index DESC LIMIT 1)
        """, (bid,))
    queries = generate_queries(BORROWER, db_path=db)
    proj = [q for q in queries if q["parameter"].startswith("Projection")]
    # Both detectors fire: EX10 (CAGR > 2x history) and the ETS band check
    assert any("Re-present MPBF and DSCR" in q["question"] for q in proj)
    assert any("sensitivity" in q["question"] for q in proj)


def test_queries_sorted_by_priority(ingested):
    data, bid, expected, db = ingested
    queries = generate_queries(BORROWER, db_path=db)
    priorities = [q["priority"] for q in queries]
    assert priorities == sorted(priorities)


def test_unknown_borrower_returns_empty(temp_db):
    assert generate_queries("Ghost Co", db_path=temp_db) == []
