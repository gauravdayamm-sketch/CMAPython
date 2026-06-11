"""Print column-C formulas for the derived rows of 3_Balance_Sheet."""
import sys
import openpyxl

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

wb = openpyxl.load_workbook("CMA_Auto_Analytics_v9.xlsx", read_only=True)
ws = wb["3_Balance_Sheet"]
rows = [11, 26, 27, 39, 40, 52, 53, 58, 63, 67, 72, 73, 81, 82, 87, 91,
        100, 102, 106, 108, 109]
cells = {}
for row in ws.iter_rows(min_row=1, max_row=110, max_col=3):
    for c in row:
        r, col = getattr(c, "row", None), getattr(c, "column_letter", None)
        if r in rows and col in ("B", "C"):
            cells[(r, col)] = c.value
for r in rows:
    label = cells.get((r, "B"), "")
    formula = cells.get((r, "C"), "")
    print(f"r{r}: {label}  ==>  {formula}")
wb.close()
