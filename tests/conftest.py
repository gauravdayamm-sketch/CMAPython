"""Shared fixtures: temporary database with the real schema, optionally seeded.

Every module under src/ resolves its own module-level DB constant, so the
fixtures monkeypatch them all to point at a throwaway SQLite file. Tests never
touch db/cma.sqlite, the network, or Ollama.
"""
import sqlite3
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def demo_wb(tmp_path_factory):
    """Synthetic CMA workbook built once per session from the real template."""
    from ingest.demo_fixture import build_demo_workbook
    path = tmp_path_factory.mktemp("cma") / "demo_cma.xlsx"
    expected = build_demo_workbook(path)
    return path, expected


@pytest.fixture
def ingested(demo_wb, temp_db):
    """Demo workbook parsed + persisted into the temp database."""
    from ingest.cma_workbook import ingest_workbook
    path, expected = demo_wb
    data, bid = ingest_workbook(path, db_path=temp_db)
    assert not data.errors()
    return data, bid, expected, temp_db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Empty database built from db/schema.sql, patched into every module."""
    db_path = tmp_path / "cma.sqlite"
    schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema)

    from analytics import anomaly, ews, forecast, ohlson, scores, wc_assessment
    from agents import committee
    from data import feeds, news_intel, registry_checks
    from ingest import cma_workbook
    for mod in (anomaly, ews, forecast, ohlson, scores, wc_assessment,
                committee, feeds, news_intel, registry_checks, cma_workbook):
        monkeypatch.setattr(mod, "DB", db_path)

    return db_path


# Five years of internally consistent financials for "Test Borrower Ltd".
# All Beneish/Z-score inputs populated (receivables, cogs, sga, ppe, ...).
FINANCIAL_YEARS = [
    # fy_end,        ta,   tl,  ca,  cl,  wc,  ni, ebitda, ffo, dep, sales, recv, cogs, sga, ppe, sec, ltd,  int
    ("2020-03-31", 1000, 450, 600, 350, 250,  90, 150, 110, 20, 1500, 180, 1050, 150, 350, 10, 100, 18),
    ("2021-03-31", 1080, 480, 640, 372, 268,  96, 160, 118, 22, 1620, 200, 1134, 162, 378, 11, 105, 19),
    ("2022-03-31", 1170, 515, 690, 400, 290, 104, 173, 128, 24, 1750, 215, 1225, 175, 408, 12, 112, 20),
    ("2023-03-31", 1265, 555, 745, 430, 315, 112, 187, 138, 25, 1890, 230, 1323, 189, 440, 13, 119, 21),
    ("2024-03-31", 1370, 600, 805, 465, 340, 121, 202, 149, 27, 2040, 252, 1428, 204, 476, 14, 127, 23),
]


@pytest.fixture
def seeded_db(temp_db):
    """temp_db plus one borrower with 5 years of financials and 12 weeks of cash flow."""
    with sqlite3.connect(temp_db) as conn:
        conn.execute("""
            INSERT INTO borrower (name, cin, industry, constitution, rbi_sector)
            VALUES ('Test Borrower Ltd', 'U00000XX0000PLC000000',
                    'Manufacturing', 'Private Limited', 'Medium Enterprises')
        """)
        bid = conn.execute(
            "SELECT borrower_id FROM borrower WHERE name='Test Borrower Ltd'"
        ).fetchone()[0]

        for (fy, ta, tl, ca, cl, wc, ni, ebitda, ffo, dep, sales,
             recv, cogs, sga, ppe, sec, ltd, intr) in FINANCIAL_YEARS:
            conn.execute("""
                INSERT INTO financials
                (borrower_id, fy_end, audited, total_assets, total_liabilities,
                 current_assets, current_liabilities, working_capital,
                 net_income, ebitda, ffo, depreciation, sales, receivables,
                 cogs, sga, ppe, securities, long_term_debt, interest_cost)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (bid, fy, ta, tl, ca, cl, wc, ni, ebitda, ffo, dep, sales,
                  recv, cogs, sga, ppe, sec, ltd, intr))

        # 12 weeks of cash flow with mild growth and noise
        for i in range(12):
            week_end = f"2024-{(4 + i // 4):02d}-{(7 * (i % 4) + 5):02d}"
            inflow  = 40 + i * 0.5 + (1.5 if i % 3 == 0 else -0.8)
            outflow = 38 + i * 0.4 + (0.9 if i % 2 == 0 else -0.5)
            conn.execute("""
                INSERT INTO cashflow_weekly
                (borrower_id, week_end, inflow, outflow, net)
                VALUES (?, ?, ?, ?, ?)
            """, (bid, week_end, inflow, outflow, inflow - outflow))

    return temp_db
