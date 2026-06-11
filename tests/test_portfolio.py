"""Tests for the portfolio summary."""
from analytics.portfolio import portfolio_summary
from ingest.demo_fixture import BORROWER


def test_portfolio_lists_cma_borrowers(ingested):
    data, bid, expected, db = ingested
    book = portfolio_summary(db_path=db)
    assert len(book) == 1
    row = book[0]
    assert row["borrower"] == BORROWER
    assert row["latest_audited"] == "2024-25"
    assert row["net_sales"] == 1815.0
    assert row["red_flags"] == 0
    assert not row["rfa_required"]
    assert row["ensemble"].count("/") == 2     # three model zones


def test_portfolio_empty_db(temp_db):
    assert portfolio_summary(db_path=temp_db) == []
