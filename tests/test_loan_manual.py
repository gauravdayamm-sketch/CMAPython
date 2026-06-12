"""Tests for the loan-manual knowledge base (index + search, no LLM)."""
from data.loan_manual import index_manual, search_manual, manual_indexed

SAMPLE = """
Chapter - 7 Working Capital Advances
Part - 1
The Maximum Permissible Bank Finance under the Second Method of Lending
requires the borrower to bring in margin of twenty five percent of total
current assets from long term sources. The minimum acceptable current
ratio under this method works out to 1.33. This stipulation protects the
liquidity position of the borrowing unit and ensures adequate net working
capital at all times during the currency of the facility sanctioned.
Chapter - 11 Term Loan
Part - 2
Debt Service Coverage Ratio for term loans: the gross DSCR should
normally not be below 1.75 averaged over the repayment period, with a
minimum of 1.25 in any single year of the loan tenor for the proposal
to be considered bankable by the sanctioning authority of the bank.
"""


def test_index_and_search(tmp_path, temp_db):
    f = tmp_path / "manual.md"
    f.write_text(SAMPLE, encoding="utf-8")
    n = index_manual(f, db_path=temp_db)
    assert n == 2
    assert manual_indexed(db_path=temp_db) == 2

    hits = search_manual("second method margin current assets",
                         db_path=temp_db, k=2)
    assert hits
    assert "Chapter 7" in hits[0]["chapter"]
    assert "1.33" in hits[0]["content"]

    hits = search_manual("DSCR term loan minimum", db_path=temp_db, k=1)
    assert "Chapter 11" in hits[0]["chapter"]


def test_search_empty_index(temp_db):
    assert search_manual("anything", db_path=temp_db) == []
    assert manual_indexed(db_path=temp_db) == 0


def test_reindex_replaces(tmp_path, temp_db):
    f = tmp_path / "manual.md"
    f.write_text(SAMPLE, encoding="utf-8")
    index_manual(f, db_path=temp_db)
    index_manual(f, db_path=temp_db)
    assert manual_indexed(db_path=temp_db) == 2     # replaced, not doubled
