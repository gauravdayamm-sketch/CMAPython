"""
Ingest every LLMS export set in a folder and run the full assessment
pipeline on each borrower. Real-data validation harness.

Usage:  python scripts/run_llms_pipeline.py "D:\\Work\\ZCC 05112025"
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingest.llms_export import (
    find_borrower_sets, ingest_llms_set, reconcile_with_bank,
)
from analytics.wc_assessment import assess_working_capital, form_v
from analytics.ews import run_full_ews
from analytics.scores import run_ensemble
from analytics.forecast import scrutinize_projections

folder = sys.argv[1] if len(sys.argv) > 1 else r"D:\Work\ZCC 05112025"
sets = find_borrower_sets(folder)
print(f"Found {len(sets)} borrower export sets: {', '.join(sets)}\n")

for prefix, files in sorted(sets.items()):
    print("=" * 72)
    print(f"### {prefix}")
    print("=" * 72)

    data, bid = ingest_llms_set(files["bs"], files["os"],
                                perf_path=files.get("perf"))
    for level, msg in data.issues:
        print(f"  [{level}] {msg}")
    if data.errors():
        print("  >> ingestion failed, skipping\n")
        continue

    name = data.borrower["name"]
    print(f"  Borrower: {name}  (CIF {data.borrower.get('cif')})")
    print(f"  Years: {', '.join(y.fy_label + ':' + y.statement_type[:4] + ('/dual' if y.is_dual else '') for y in data.years)}")

    # Reconciliation vs the bank's own Performance file
    rec = reconcile_with_bank(data)
    if rec:
        mismatches = [r for r in rec if not r["match"]]
        print(f"\n  RECONCILIATION vs bank-computed indicators: "
              f"{len(rec) - len(mismatches)}/{len(rec)} match")
        for r in mismatches:
            print(f"    ✗ {r['fy']:<22} {r['metric']:<15} "
                  f"bank={r['bank']:<10} engine={r['engine']}")

    a = assess_working_capital(name)
    latest = a.latest_audited
    if latest:
        m = latest["metrics"]
        print(f"\n  FORM V ({latest['fy_label']} audited):  "
              f"WCG={m['wcg']}  NWC={m['nwc']}  "
              f"MPBF-II={m['mpbf_method2']}  ABF={m['abf']}")
        print(f"  Ratios: CR={m['current_ratio']}  TOL/TNW={m['tol_tnw']}  "
              f"ICR={m['icr']}  DSO={m['dso']}  InvDays={m['inventory_days']}")
    breaches = [v for v in a.verdicts if v["status"] == "BREACH"]
    print(f"  Threshold breaches: {len(breaches)}"
          + (f"  ({', '.join(sorted(set(v['metric'] for v in breaches)))})"
             if breaches else ""))

    ews = run_full_ews(name)
    print(f"\n  RED FLAGS: {ews.red_flag_verdict}")
    for f in ews.triggered_red_flags + ews.triggered_exceptions:
        print(f"    ⚠ {f.rule_id} {f.test} = {f.value}")
    auto_hits = [i for i in ews.ews_indicators if i["triggered"]]
    for i in auto_hits:
        print(f"    ⚠ EWS#{i['id']} {i['indicator']} = {i['value']}")
    if ews.rfa_required:
        print(f"    !! RFA: {ews.rfa_reason}")

    ens = run_ensemble(name)
    if not ens.error:
        parts = [f"{s.model.split(' ')[0]}={s.zone}"
                 for s in (ens.ohlson, ens.altman, ens.zmijewski) if s]
        print(f"\n  ENSEMBLE: {' | '.join(parts)}  =>  {ens.verdict}")

    scr = scrutinize_projections(name)
    if not scr["error"]:
        for metric, entry in scr["metrics"].items():
            if entry["error"]:
                print(f"  SCRUTINY {metric}: {entry['error']}")
            else:
                verdicts = ", ".join(
                    f"{p['fy_label']}:{p['verdict']}" for p in entry["projections"])
                print(f"  SCRUTINY {metric}: {verdicts}")
    print()
