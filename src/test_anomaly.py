"""Test anomaly detection module."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from analytics.anomaly import run_all_anomaly, save_anomaly_to_db

print("Running anomaly detection on Acme Manufacturing...")
print()

report = run_all_anomaly()

print(f"Borrower:  {report.borrower_name}")
print(f"FY:        {report.fy_end}")
print(f"Master:    {report.master_verdict}")
print()

print("Rolling Z-Scores:")
if report.z_scores:
    for z in report.z_scores:
        print(f"  {z.metric:<30} Z={z.z_score:+.2f}  {z.verdict}")
else:
    print("  Not enough data (need 4+ years)")

print()
print("Beneish M-Score:")
if report.beneish:
    print(f"  M-Score:  {report.beneish.m_score}")
    print(f"  Verdict:  {report.beneish.verdict}")
    print(f"  Components:")
    for k, v in report.beneish.components.items():
        print(f"    {k:<6} {v:+.4f}")

print()
print("IsolationForest:")
if report.isoforest:
    print(f"  Score:    {report.isoforest.score}")
    print(f"  Verdict:  {report.isoforest.verdict}")

print()
save_anomaly_to_db(report)
print("Results saved to SQLite.")