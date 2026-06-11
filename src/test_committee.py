"""Test the 4-agent credit committee."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Windows consoles default to cp1252; LLM output can contain any Unicode
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agents.committee import run_committee
import time

print("Running 4-agent Credit Committee...")
print("(Each agent takes 10-30 seconds on first run)")
print()

start = time.time()
result = run_committee()
elapsed = time.time() - start

print()
print("=" * 60)
print(f"COMMITTEE COMPLETED in {elapsed:.0f} seconds")
print("=" * 60)
print(f"Borrower:   {result.borrower_name}")
print(f"Ohlson PD:  {result.ohlson_pd:.1%}")
print(f"Verdict:    {result.verdict}")
print(f"Confidence: {result.confidence}")
print(f"Key Risk:   {result.key_risk}")
print()
print("FINAL MEMO:")
print("-" * 60)
print(result.final_memo)
print()
print("AUDIT LOG:")
for entry in result.audit_log:
    status = "OK" if not entry["error"] else f"ERROR: {entry['error']}"
    print(f"  {entry['agent']:<12} {entry['model']:<20} {status}")