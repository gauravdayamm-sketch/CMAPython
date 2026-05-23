"""Test forecasting module."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from analytics.forecast import run_annual_forecast, run_weekly_forecast

print("ANNUAL FORECAST — Acme Manufacturing")
print("-" * 60)
results = run_annual_forecast()

if not results:
    print("No results — need 4+ years of data in SQLite")
else:
    for r in results:
        if r.error:
            print(f"  {r.metric:<30} ERROR: {r.error}")
            continue
        yr1 = r.forecast[0] if r.forecast else None
        yr2 = r.forecast[1] if len(r.forecast) > 1 else None
        print(f"  {r.metric:<30} "
              f"Yr+1={yr1.point if yr1 else '—':>10}  "
              f"Yr+2={yr2.point if yr2 else '—':>10}  "
              f"SMAPE={r.smape:.1%}  {r.verdict}")

print()
print("WEEKLY FORECAST")
print("-" * 60)
weekly = run_weekly_forecast(opening_cash=50.0, cc_limit=80.0)
print(f"  Verdict: {weekly.verdict}")
if weekly.receipts_forecast:
    print(f"  Weeks forecast: {weekly.horizon_weeks}")
    print(f"  Sample (first 3 weeks):")
    for i in range(min(3, len(weekly.receipts_forecast))):
        print(f"    Wk +{i+1}: "
              f"In={weekly.receipts_forecast[i]:.1f}  "
              f"Out={weekly.payments_forecast[i]:.1f}  "
              f"Net={weekly.net_forecast[i]:.1f}  "
              f"Cum={weekly.cumulative[i]:.1f}")