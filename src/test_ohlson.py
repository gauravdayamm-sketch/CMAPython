import sys
import pathlib

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from analytics.ohlson import compute_ohlson

# Test 1: Healthy borrower
print("=" * 50)
print("TEST 1: HEALTHY BORROWER")
print("=" * 50)
result = compute_ohlson(
    ta=1500, tl=450, ca=700, cl=350,
    wc=350, ni=180, ffo=220,
    ni_prev=150
)
print(f"O-Score:          {result.o_score}")
print(f"Prob of Default:  {result.prob_default:.1%}")
print(f"Risk Band:        {result.risk_band}")
print(f"\nContributions:")
for k, v in result.contributions.items():
    print(f"  {k:<10} {v:+.4f}")

# Test 2: Distressed borrower
print()
print("=" * 50)
print("TEST 2: DISTRESSED BORROWER")
print("=" * 50)
result2 = compute_ohlson(
    ta=800, tl=700, ca=300, cl=400,
    wc=-100, ni=-30, ffo=-10,
    ni_prev=-20
)
print(f"O-Score:          {result2.o_score}")
print(f"Prob of Default:  {result2.prob_default:.1%}")
print(f"Risk Band:        {result2.risk_band}")