"""Test cascade + Monte Carlo module."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Windows consoles default to cp1252, which cannot print symbols like ₹
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from analytics.cascade import run_cascade_from_dict, SCENARIOS

# Acme Manufacturing financials (FY2024)
financials = {
    "sales":          2000,
    "opm":            0.10,     # 10% OPM
    "ebitda":         200,
    "dso":            33,       # receivables/sales*365
    "inv_days":       28,
    "depreciation":   26,
    "interest":       20,
    "tl_interest":    15,
    "tl_instalment":  20,
    "stbb":           80,
    "total_debt":     200,
    "cash_accruals":  146,
}

for scenario in SCENARIOS:
    result = run_cascade_from_dict(financials, scenario_name=scenario)

    if "error" in result:
        print(f"{scenario}: ERROR — {result['error']}")
        continue

    be = result["breakeven_severity"]
    be_str = f"{be:.0%}" if be is not None else "survives all"

    print(
        f"{scenario:<28} "
        f"Base={result['dscr_base']:.2f}x  "
        f"P50={result['dscr_p50']:.2f}x  "
        f"P5={result['dscr_p5']:.2f}x  "
        f"P(fail)={result['prob_failure']:.1%}  "
        f"Breakeven={be_str}"
    )

print()
print("Detailed Monte Carlo — Recession scenario:")
r = run_cascade_from_dict(financials, scenario_name="Recession")
print(f"  Verdict:   {r['verdict']}")
print("  DSCR distribution:")
print(f"    P5  (worst 5%%):  {r['dscr_p5']:.2f}x")
print(f"    P25:              {r['dscr_p25'] if 'dscr_p25' in r else 'n/a'}")
print(f"    P50 (median):     {r['dscr_p50']:.2f}x")
print(f"    P95 (best 5%%):   {r['dscr_p95']:.2f}x")
print(f"  Prob DSCR < 1.0:   {r['prob_failure']:.1%}")
print(f"  Prob < covenant:   {r['prob_breach']:.1%}")
print(f"  Breakeven severity:{r['breakeven_severity']}")
print()
print("Severity Ladder (Recession):")
print(f"  {'Severity':>10}  {'DSCR':>8}  {'EBITDA':>10}  {'WC Gap':>8}  Verdict")
for pt in r["ladder"]:
    print(
        f"  {pt['severity']:>10.0%}  "
        f"{pt['dscr']:>8.2f}x  "
        f"{pt['ebitda']:>10.1f}  "
        f"{pt['wc_gap']:>8.1f}  "
        f"{pt['verdict']}"
    )
