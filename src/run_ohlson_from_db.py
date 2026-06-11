"""
Reads financial data from SQLite and runs Ohlson O-Score.
Saves results back to anomaly_result table.
"""
import sqlite3
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Windows consoles default to cp1252, which cannot print symbols like ↑/↓
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from analytics.ohlson import compute_ohlson, save_to_db

DB = pathlib.Path(__file__).resolve().parent.parent / "db" / "cma.sqlite"

def run_ohlson_for_borrower(borrower_name):
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        # Get borrower
        borrower = conn.execute(
            "SELECT * FROM borrower WHERE name = ?",
            (borrower_name,)
        ).fetchone()

        if not borrower:
            print(f"Borrower '{borrower_name}' not found in database")
            return

        borrower_id = borrower["borrower_id"]
        print(f"Running Ohlson for: {borrower['name']}")
        print(f"Industry: {borrower['industry']}")
        print()

        # Get all financial years ordered
        rows = conn.execute("""
            SELECT * FROM financials
            WHERE borrower_id = ?
            ORDER BY fy_end
        """, (borrower_id,)).fetchall()

        if not rows:
            print("No financial data found")
            return

        # Run Ohlson for each year
        prev_ni = None
        print(f"{'FY':<15} {'O-Score':>10} {'PD%':>8} {'Band':<20}")
        print("-" * 55)

        for i, row in enumerate(rows):
            result = compute_ohlson(
                ta  = row["total_assets"],
                tl  = row["total_liabilities"],
                ca  = row["current_assets"],
                cl  = row["current_liabilities"],
                wc  = row["working_capital"],
                ni  = row["net_income"],
                ffo = row["ffo"],
                ni_prev = prev_ni
            )

            if result:
                print(f"{row['fy_end']:<15} "
                      f"{result.o_score:>10.4f} "
                      f"{result.prob_default:>7.1%} "
                      f"{result.risk_band:<20}")

                # Save to database
                save_to_db(borrower_id, row["fy_end"], result)

            prev_ni = row["net_income"]

        print()
        print("Results saved to anomaly_result table in SQLite.")

        # Show top risk driver for latest year
        latest = rows[-1]
        result_latest = compute_ohlson(
            ta  = latest["total_assets"],
            tl  = latest["total_liabilities"],
            ca  = latest["current_assets"],
            cl  = latest["current_liabilities"],
            wc  = latest["working_capital"],
            ni  = latest["net_income"],
            ffo = latest["ffo"],
            ni_prev = rows[-2]["net_income"] if len(rows) > 1 else None
        )

        if result_latest:
            print(f"\nTop risk drivers (latest year {latest['fy_end']}):")
            sorted_contribs = sorted(
                result_latest.contributions.items(),
                key=lambda x: abs(x[1]),
                reverse=True
            )
            for label, value in sorted_contribs[:5]:
                direction = "↑ risk" if value > 0 else "↓ risk"
                print(f"  {label:<10} {value:+.4f}  {direction}")

if __name__ == "__main__":
    run_ohlson_for_borrower("Acme Manufacturing Ltd")