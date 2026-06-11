"""
CMA Analyser — one command for the whole pipeline.

  python cma.py ingest <folder | workbook.xlsx>   pull borrower data in
  python cma.py assess [-b NAME]                  full on-screen assessment
  python cma.py memo   [-b NAME]                  Word credit memorandum
  python cma.py portfolio                         whole book, worst first
  python cma.py news   [-b NAME]                  adverse-media sweep
  python cma.py registry NAME [--record REG STATUS [REMARKS]]
  python cma.py committee [-b NAME]               4-agent AI narrative (Ollama)
  python cma.py serve                             REST API on 127.0.0.1:8000
  python cma.py demo                              synthetic end-to-end demo

Run any command with -h for details. -b defaults to the most recently
ingested borrower.
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cmd_ingest(args):
    target = pathlib.Path(args.path)
    if target.is_dir():
        from ingest.llms_export import find_borrower_sets, ingest_llms_set, \
            reconcile_with_bank
        sets = find_borrower_sets(target)
        if not sets:
            sys.exit("No LLMS export sets (…CMA_Balance_Sheet / "
                     "…CMA_Operating_Statement) found in that folder.")
        for prefix, files in sorted(sets.items()):
            data, bid = ingest_llms_set(files["bs"], files["os"],
                                        perf_path=files.get("perf"))
            if data.errors():
                print(f"✗ {prefix}: " + "; ".join(data.errors()))
                continue
            rec = reconcile_with_bank(data)
            matched = sum(1 for r in rec if r["match"])
            print(f"✓ {data.borrower['name']}  "
                  f"({len(data.years)} year-columns"
                  + (f", reconciliation {matched}/{len(rec)}" if rec else "")
                  + ")")
    elif target.suffix.lower() in (".xlsx", ".xlsm"):
        from ingest.cma_workbook import ingest_workbook
        data, bid = ingest_workbook(target)
        if data.errors():
            sys.exit("✗ " + "; ".join(data.errors()))
        print(f"✓ {data.borrower['name']} ({len(data.years)} year-columns)")
    else:
        sys.exit("Give a folder of LLMS exports or a CMA workbook .xlsx")
    print("\nNext:  python cma.py assess   |   python cma.py memo")


def cmd_assess(args):
    from analytics.wc_assessment import assess_working_capital, form_v
    from analytics.ews import run_full_ews
    from analytics.scores import run_ensemble
    from analytics.forecast import scrutinize_projections
    from report.committee_queries import generate_queries

    a = assess_working_capital(args.borrower)
    if a.error:
        sys.exit(a.error)
    name = a.borrower_name
    print(f"═══ {name} ═══")
    latest = a.latest_audited
    if latest:
        print(f"\nFORM V — MPBF ({latest['fy_label']} audited, ₹ Cr)")
        for label, value in form_v(a):
            print(f"  {label:<50} {value:>10,.2f}")
        m = latest["metrics"]
        print(f"\nKEY RATIOS:  CR={m['current_ratio']}  TOL/TNW={m['tol_tnw']}"
              f"  ICR={m['icr']}  DSO={m['dso']}d  Inv={m['inventory_days']}d"
              f"  AvgDSCR={a.dscr_avg_gross}x")

    ews = run_full_ews(name)
    if not ews.error:
        print(f"\nRED FLAGS: {ews.red_flag_verdict}")
        for f in ews.triggered_red_flags + ews.triggered_exceptions:
            print(f"  ⚠ {f.rule_id:<5} {f.test}")
        if ews.rfa_required:
            print(f"  !! RFA REQUIRED: {ews.rfa_reason}")

    ens = run_ensemble(name)
    if not ens.error:
        print(f"\nENSEMBLE: {ens.verdict}")

    scr = scrutinize_projections(name)
    if not scr["error"]:
        for metric, e in scr["metrics"].items():
            if not e["error"]:
                worst = max((p["verdict"] for p in e["projections"]),
                            key=["IN LINE", "CONSERVATIVE", "VERY CONSERVATIVE",
                                 "OPTIMISTIC", "AGGRESSIVE"].index,
                            default="—")
                print(f"PROJECTIONS {metric}: {worst}")

    queries = generate_queries(name)
    print(f"\nCOMMITTEE QUERIES: {len(queries)} "
          f"(full text in the memo — python cma.py memo)")
    for q in queries[:5]:
        print(f"  [P{q['priority']}] {q['parameter']}: {q['question'][:90]}")


def cmd_memo(args):
    from report.memo_docx import generate_credit_memo
    try:
        path = generate_credit_memo(args.borrower)
    except ValueError as e:
        sys.exit(str(e))
    print(f"✓ Memo written: {path}")


def cmd_portfolio(args):
    from analytics.portfolio import portfolio_summary
    book = portfolio_summary()
    if not book:
        sys.exit("No borrowers with CMA data. Start with: python cma.py ingest")
    print(f"{'Borrower':<38}{'FY':<9}{'Sales':>8}{'CR':>6}{'TOL/TNW':>9}"
          f"{'DSCR':>6}{'Flags':>6}{'Models':>7}{'News':>5}  RFA")
    print("-" * 105)
    for r in book:
        print(f"{r['borrower'][:36]:<38}{r['latest_audited'] or '—':<9}"
              f"{r['net_sales'] or 0:>8,.0f}"
              f"{r['current_ratio'] or 0:>6.2f}{r['tol_tnw'] or 0:>9.2f}"
              f"{r['avg_gross_dscr'] or 0:>6.2f}{r['red_flags'] or 0:>6}"
              f"{r['ensemble']:>7}{r['adverse_news']:>5}"
              f"  {'⚠ YES' if r['rfa_required'] else '—'}")


def cmd_news(args):
    from data.news_intel import refresh_borrower_news, sweep_all_borrowers
    if args.borrower:
        r = refresh_borrower_news(args.borrower)
        if r["error"]:
            sys.exit(r["error"])
        print(f"{args.borrower}: {r['fetched']} fetched, {r['new']} new, "
              f"{r['total_adverse']} adverse on record")
    else:
        for name, r in sweep_all_borrowers().items():
            print(f"{name[:40]:<42} new={r['new']} "
                  f"adverse={r.get('total_adverse', 0)} {r['error'][:40]}")


def cmd_registry(args):
    from data.registry_checks import get_checks, record_check
    if args.record:
        registry, status = args.record[0], args.record[1]
        remarks = args.record[2] if len(args.record) > 2 else ""
        try:
            record_check(args.name, registry, status, remarks=remarks)
        except ValueError as e:
            sys.exit(str(e))
        print(f"✓ recorded {registry} = {status}")
    checks = get_checks(args.name)
    if not checks:
        sys.exit(f"Borrower not found: {args.name}")
    print(f"Registry due diligence — {args.name}")
    for c in checks:
        print(f"  {c['status'].upper():<10} {c['label']:<34} "
              f"{c['checked_on'] or ''} {c['remarks'][:40]}")
        if c["status"] in ("unchecked", "stale"):
            print(f"             → {c['url']}")


def cmd_committee(args):
    from agents.committee import run_committee
    print("Running the 4-agent committee (needs Ollama; 60–120 s)...")
    result = run_committee(borrower_name=args.borrower)
    if result.error:
        sys.exit(result.error)
    print(f"\nVerdict: {result.verdict}  (confidence {result.confidence})")
    print(f"Key risk: {result.key_risk}\n")
    print(result.final_memo)
    if result.number_audit and result.number_audit["unverified"]:
        print(f"\n!! {len(result.number_audit['unverified'])} figures could "
              f"not be traced to source data — verify before relying on them.")
    print("\nRe-export the memo to include this narrative: python cma.py memo")


def cmd_benchmark(args):
    from analytics.benchmark import benchmark
    from analytics.wc_assessment import assess_working_capital
    name = args.borrower
    if name is None:
        a = assess_working_capital(None)
        if a.error:
            sys.exit(a.error)
        name = a.borrower_name
    r = benchmark(name)
    if r["error"]:
        sys.exit(r["error"])
    print(f"═══ {r['borrower']} vs {r['peer_set']} "
          f"[{r['n_peers']} peers] ═══")
    print(f"{'Metric':<20}{'Borrower':>10}{'Peer med.':>11}{'%ile':>6}"
          f"{'Norm':>8}  Source")
    for row in r["rows"]:
        fmt = (lambda v: "—" if v is None else
               (f"{v:.1%}" if row["key"] in
                ("ebitda_margin", "pat_margin", "sales_growth")
                else f"{v:.1f}" if row["key"] == "dso" else f"{v:.2f}"))
        pct = f"{row['percentile']}" if row["percentile"] is not None else "—"
        print(f"{row['metric']:<20}{fmt(row['value']):>10}"
              f"{fmt(row['peer_median']):>11}{pct:>6}"
              f"{fmt(row['norm_median']):>8}  {row['norm_source'] or ''}")
    if r["weak_metrics"]:
        print(f"\nBottom quartile of peers on: {', '.join(r['weak_metrics'])}")


def cmd_norms(args):
    from analytics.benchmark import set_norms, get_norms
    if args.set:
        values = {}
        for pair in args.set:
            k, _, v = pair.partition("=")
            values[k] = float(v)
        set_norms(args.industry, values, source=args.source or "")
        print(f"✓ norms recorded for {args.industry}")
    for n in get_norms(args.industry if args.industry != "--list" else None):
        print(f"  {n['industry']:<22}{n['metric']:<16}{n['median']:<10}"
              f"{n['updated_on']}  {n['source']}")


def cmd_industry(args):
    from analytics.benchmark import set_industry
    try:
        set_industry(args.name, args.industry)
    except ValueError as e:
        sys.exit(str(e))
    print(f"✓ {args.name} → industry '{args.industry}'")


def cmd_notecheck(args):
    from report.note_check import check_note
    r = check_note(args.path, borrower_name=args.borrower)
    if r["error"]:
        sys.exit(r["error"])
    print(f"Proposal-note check vs CMA data of: {r['borrower']}")
    print(f"  Figures found: {r['total']}   traced: {r['traced']}   "
          f"untraced: {len(r['untraced'])}"
          + (f"   ({r['trace_rate']:.0%} traced)" if r["trace_rate"] is not None
             else ""))
    if r["untraced"]:
        print("\n  VERIFY WITH THE ANALYST (figure ← context):")
        for u in r["untraced"][:20]:
            print(f"  • {u['figure']}  ←  …{u['context'][:100]}…")
    else:
        print("  Every figure in the note traces to the CMA data. ✓")


def cmd_serve(args):
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000,
                app_dir=str(ROOT / "src"))


def cmd_demo(args):
    import runpy
    sys.argv = ["test_cma.py"]
    runpy.run_path(str(ROOT / "src" / "test_cma.py"), run_name="__main__")


def main():
    p = argparse.ArgumentParser(
        prog="cma", description="CMA Analyser — credit assessment pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="ingest LLMS export folder or CMA workbook")
    s.add_argument("path")
    s.set_defaults(fn=cmd_ingest)

    for name, fn, hlp in (("assess", cmd_assess, "on-screen assessment"),
                          ("memo", cmd_memo, "generate Word memorandum"),
                          ("news", cmd_news, "adverse-media sweep"),
                          ("committee", cmd_committee, "AI committee narrative")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("-b", "--borrower", default=None,
                       help="borrower name (default: latest ingested)")
        s.set_defaults(fn=fn)

    s = sub.add_parser("portfolio", help="whole book at a glance")
    s.set_defaults(fn=cmd_portfolio)

    s = sub.add_parser("benchmark", help="borrower vs the book + industry norms")
    s.add_argument("-b", "--borrower", default=None)
    s.set_defaults(fn=cmd_benchmark)

    s = sub.add_parser("norms", help="maintain industry medians")
    s.add_argument("industry", help='industry name, or --list')
    s.add_argument("--set", nargs="+", metavar="METRIC=VALUE",
                   help="e.g. ebitda_margin=0.07 current_ratio=1.25")
    s.add_argument("--source", default="", help='e.g. "CRISIL Apr 2026"')
    s.set_defaults(fn=cmd_norms)

    s = sub.add_parser("industry", help="tag a borrower's industry")
    s.add_argument("name")
    s.add_argument("industry")
    s.set_defaults(fn=cmd_industry)

    s = sub.add_parser("notecheck",
                       help="trace every figure in an analyst's .docx note "
                            "to the CMA data")
    s.add_argument("path", help="path to the proposal note .docx")
    s.add_argument("-b", "--borrower", default=None)
    s.set_defaults(fn=cmd_notecheck)

    s = sub.add_parser("registry", help="registry due-diligence checks")
    s.add_argument("name", help="borrower name")
    s.add_argument("--record", nargs="+", metavar="ARG",
                   help="REGISTRY STATUS [REMARKS]  e.g. gst adverse \"3B gaps\"")
    s.set_defaults(fn=cmd_registry)

    s = sub.add_parser("serve", help="run the REST API")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("demo", help="synthetic end-to-end demo")
    s.set_defaults(fn=cmd_demo)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
