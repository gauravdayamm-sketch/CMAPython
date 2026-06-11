"""CMA pipeline demo: ingest a filled CMA workbook and print the assessment.

Uses CMA_Auto_Analytics_v9.xlsx directly if its 0_Setup has a borrower name;
otherwise generates a synthetic demo copy under data/ first.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Windows consoles default to cp1252, which cannot print symbols like ₹
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingest.cma_workbook import parse_workbook, ingest_workbook, ROOT
from ingest.demo_fixture import build_demo_workbook
from analytics.wc_assessment import assess_working_capital, form_v
from analytics.forecast import scrutinize_projections

TEMPLATE = ROOT / "CMA_Auto_Analytics_v9.xlsx"
DEMO     = ROOT / "data" / "demo_CMA_filled.xlsx"


def pick_workbook():
    probe = parse_workbook(TEMPLATE)
    if not probe.errors():
        return TEMPLATE
    print("Template has no borrower data — generating synthetic demo copy...")
    DEMO.parent.mkdir(exist_ok=True)
    build_demo_workbook(DEMO)
    return DEMO


path = pick_workbook()
print(f"Ingesting: {path.name}")
data, bid = ingest_workbook(path)

for level, msg in data.issues:
    print(f"  [{level}] {msg}")
if data.errors():
    sys.exit("Ingestion failed.")

print(f"Borrower: {data.borrower['name']} (id={bid})")
print(f"Years ingested: {len(data.years)}  "
      f"({', '.join(y.fy_label + ':' + y.statement_type[:4] for y in data.years)})")

a = assess_working_capital(data.borrower["name"])

print()
print("=" * 64)
print(f"FORM V — MPBF  (latest audited: {a.latest_audited['fy_label']})")
print("=" * 64)
for label, value in form_v(a):
    print(f"  {label:<50} {value:>10,.2f}")

print()
print("DSCR BY YEAR")
for y in a.years:
    m = y["metrics"]
    if m["gross_dscr"] is None:
        continue
    dual = " (dual est)" if y["is_dual"] else ""
    print(f"  {y['fy_label']} [{y['statement_type'][:4]}]{dual:<12} "
          f"Gross={m['gross_dscr']:.2f}x  Net={m['net_dscr']:.2f}x")
print(f"  Average Gross DSCR: {a.dscr_avg_gross:.2f}x")

latest = a.latest_audited["metrics"]
print()
print("KEY RATIOS (latest audited)")
for k, label in [("current_ratio", "Current Ratio"), ("tol_tnw", "TOL/TNW"),
                 ("icr", "ICR"), ("debt_ebitda", "Debt/EBITDA"),
                 ("inventory_days", "Inventory Days"), ("dso", "DSO"),
                 ("dpo", "DPO"), ("cash_conversion_cycle", "CCC")]:
    print(f"  {label:<16} {latest[k]}")

breaches = [v for v in a.verdicts if v["status"] == "BREACH"]
print()
print(f"THRESHOLD BREACHES: {len(breaches)}")
for v in breaches[:10]:
    print(f"  {v['fy_label']} [{v['statement_type'][:4]}] {v['metric']}: "
          f"{v['value']} vs {v['direction']} {v['threshold']}")

print()
print(f"RED FLAGS: {len(a.red_flags) or 'none'}")
for f in a.red_flags:
    print(f"  ⚠ {f}")

from analytics.ews import run_full_ews
from analytics.scores import run_ensemble
from report.memo_docx import generate_credit_memo

ens = run_ensemble(data.borrower["name"])
print()
print(f"SCORE ENSEMBLE: {ens.verdict}")
for s in (ens.ohlson, ens.altman, ens.zmijewski):
    if s:
        p = f"  p={s.prob:.1%}" if s.prob is not None else ""
        print(f"  {s.model:<22} {s.score:>9}{p}  [{s.zone}]")

ews = run_full_ews(data.borrower["name"])
print()
print(f"RED FLAG DASHBOARD: {ews.red_flag_verdict}")
print(f"  Triggered: {len(ews.triggered_red_flags)}/32 red flags, "
      f"{len(ews.triggered_exceptions)}/13 exception tests")
for f in ews.triggered_red_flags + ews.triggered_exceptions:
    print(f"  ⚠ {f.rule_id} {f.test} = {f.value}")
print(f"  RFA: {'REQUIRED — ' + ews.rfa_reason if ews.rfa_required else 'not indicated'}")

print()
print("PROJECTION SCRUTINY (borrower projections vs ETS bands)")
scr = scrutinize_projections(data.borrower["name"])
for metric, entry in scr["metrics"].items():
    if entry["error"]:
        print(f"  {metric}: {entry['error']}")
        continue
    cagr = (f"hist CAGR {entry['hist_cagr']:.1%} vs "
            f"proj CAGR {entry['proj_cagr']:.1%}"
            if entry["hist_cagr"] is not None
            and entry["proj_cagr"] is not None else "")
    note = f"  ({entry['note']})" if entry["note"] else ""
    print(f"  {metric}: {cagr}{note}")
    for p in entry["projections"]:
        print(f"    {p['fy_label']}  {p['value']:>9,.1f}  "
              f"[ETS {p['lo_80']:,.0f}–{p['hi_80']:,.0f}]  {p['verdict']}")

print()
memo_path = generate_credit_memo(data.borrower["name"])
print(f"CREDIT MEMORANDUM EXPORTED: {memo_path}")
