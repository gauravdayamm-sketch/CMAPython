"""Regenerate memos for ingested real borrowers and preview their queries."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from report.memo_docx import generate_credit_memo
from report.committee_queries import generate_queries

NAMES = sys.argv[1:] or [
    "ARROWIN METALTECH (INDIA) PRIVATE LIMITED",
    "RAMDEV SILICATE",
    "Wintas Textiles",
    "BHT FORGE INDIA PRIVATE LIMITED",
]

for name in NAMES:
    try:
        queries = generate_queries(name)
        path = generate_credit_memo(name)
        print(f"{name[:36]:<38} {len(queries):>2} queries -> {path.name}")
    except ValueError as e:
        print(f"{name[:36]:<38} SKIP: {e}")

print("\nSample — first 3 queries for", NAMES[0])
for q in generate_queries(NAMES[0])[:3]:
    print(f"  [P{q['priority']}] {q['parameter']}: {q['observation']}")
    print(f"      ASK: {q['question']}")
