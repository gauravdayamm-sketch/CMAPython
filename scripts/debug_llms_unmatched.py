"""Show export rows with numbers that matched no label rule — mapping gaps."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingest.llms_export import (
    read_table, _parse_header, _norm, _num, BS_LABELS, OS_LABELS, BS_TOTAL_CHECKS, OS_TOTAL_CHECKS,
    SECTION_KEYWORDS, DATA_COL0,
)

path = pathlib.Path(sys.argv[1])
label_map, total_checks = (
    (BS_LABELS, BS_TOTAL_CHECKS) if "Balance" in path.name
    else (OS_LABELS, OS_TOTAL_CHECKS))

rows = read_table(path)
_, _, cols = _parse_header(rows)
section = ""
for row in rows:
    label = _norm(row[1] if len(row) > 1 else "")
    if not label:
        continue
    nums = [(i, _num(row[i])) for i in range(DATA_COL0, len(row))
            if _num(row[i]) is not None]
    if not nums:
        for kw in SECTION_KEYWORDS:
            if label.startswith(kw) or kw in label[:40]:
                section = label
                break
        continue
    matched = any(
        (sec is None or sec in section) and label.startswith(pat)
        for sec, pat, code in label_map
    ) or any(label.startswith(pat) for pat, _ in total_checks)
    if not matched:
        print(f"UNMATCHED [{section[:30]}] '{label}' -> "
              f"{[(i, v) for i, v in nums]}")
