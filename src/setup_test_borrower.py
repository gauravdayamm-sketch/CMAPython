"""
Creates a test borrower and financial data in SQLite.
Run this once to populate the database for testing.
"""
import sqlite3
import pathlib

DB = pathlib.Path(__file__).resolve().parent.parent / "db" / "cma.sqlite"

def setup_test_data():
    with sqlite3.connect(DB) as conn:

        # Insert test borrower
        conn.execute("""
            INSERT OR IGNORE INTO borrower
            (name, cin, industry, constitution, rbi_sector)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "Acme Manufacturing Ltd",
            "U12345MH2010PLC123456",
            "Auto Components",
            "Private Limited",
            "Medium Enterprises"
        ))

        # Get borrower_id
        borrower_id = conn.execute(
            "SELECT borrower_id FROM borrower WHERE name = ?",
            ("Acme Manufacturing Ltd",)
        ).fetchone()[0]

        print(f"Borrower ID: {borrower_id}")

        # Insert 4 years of financial data
        years = [
            # fy_end, ta, tl, ca, cl, wc, ni, ebitda, ffo, dep, sales
            ("2021-03-31", 1000, 450, 600, 350, 250, 90,  150, 110, 20, 1500),
            ("2022-03-31", 1100, 495, 660, 385, 275, 99,  165, 121, 22, 1650),
            ("2023-03-31", 1210, 545, 726, 424, 302, 109, 182, 133, 24, 1815),
            ("2024-03-31", 1330, 599, 799, 466, 333, 120, 200, 146, 26, 2000),
        ]

        for fy, ta, tl, ca, cl, wc, ni, ebitda, ffo, dep, sales in years:
            conn.execute("""
                INSERT OR IGNORE INTO financials
                (borrower_id, fy_end, audited,
                 total_assets, total_liabilities,
                 current_assets, current_liabilities,
                 working_capital, net_income, ebitda,
                 ffo, depreciation, sales)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (borrower_id, fy, ta, tl, ca, cl, wc, ni, ebitda, ffo, dep, sales))

        print("Inserted 4 years of financial data")

        # Verify
        rows = conn.execute("""
            SELECT fy_end, total_assets, net_income, ffo
            FROM financials
            WHERE borrower_id = ?
            ORDER BY fy_end
        """, (borrower_id,)).fetchall()

        print("\nData in database:")
        print(f"  {'FY':<15} {'TA':>10} {'NI':>10} {'FFO':>10}")
        for row in rows:
            print(f"  {row[0]:<15} {row[1]:>10} {row[2]:>10} {row[3]:>10}")

if __name__ == "__main__":
    setup_test_data()
    print("\nTest borrower setup complete.")