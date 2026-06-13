# CMA Analyser — User Guide

*A desk officer's manual. Version 1.0 — June 2026.*

## What this is

CMA Analyser turns a borrower's CMA data into a complete, committee-ready
credit assessment in about a minute: Form V working-capital assessment
(MPBF), DSCR, the full ratio suite, 32 red flags, 13 forensic exception
tests, RBI Early Warning Signals with the Red-Flagged-Account rule, a
three-model bankruptcy-score ensemble, statistical scrutiny of the
borrower's projections, adverse-media screening, registry due-diligence
tracking, an AI-drafted narrative — and the pointed questions to put to
the analyst presenting the proposal. Everything is recomputed from the
data on every run, every number in every output traces to a source line,
and nothing about your borrowers ever leaves your PC.

All commands below are typed in PowerShell from the `D:\CMA_Python` folder.

---

## 1. One-time setup (already done on this PC)

For a new machine:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\init_db.py            # creates/migrates the local database
```

For the AI narrative (optional — everything else works without it),
install Ollama and pull the three local models:

```powershell
ollama pull llama3.2:3b
ollama pull qwen2.5:7b
ollama pull llama3.1:8b
```

---

## 1A. The one-click way (recommended)

For a complete memo in one step, use the **`workspace`** folders and the
**`RUN_CMA.bat`** launcher:

```
workspace\
   cma\          ← drop this borrower's LLMS exports (or a .xlsx)
   probe42\      ← drop the Probe42 report / Form AOC-4 / audit report PDFs
   notes\        ← drop the proposal note .docx   (optional; local-only)
   loan_manual\  ← put the loan manual here once   (indexed, then left alone)
   output\       ← the finished memo appears here
```

1. Drop one borrower's files into `cma\` and `probe42\` (and `notes\` if
   permitted). Each folder has a README explaining what it accepts.
2. **Double-click `RUN_CMA.bat`** in the main folder.
3. Watch the eight steps run in the window — CMA ingest, MCA filings,
   note check, assessment, AI committee, memo. The memo lands in
   `output\` and the window stays open so you can read the progress.

Process one borrower at a time; clear `cma\` and `probe42\` before the
next. Equivalent command line: `.\cma autorun` (add `--no-committee` to
skip the AI narrative and run in ~30s without Ollama).

The sections below describe the same steps run individually, for when you
want to do just one piece.

## 2. The daily workflow: from proposal to question sheet

### Step 1 — Export the borrower from LLMS

From the bank system, export the three statements for the borrower as
`.xls` (or `.csv`) into one folder, keeping the system's file names:

- `<Name> CMA_Balance_Sheet.xls`
- `<Name> CMA_Operating_Statement.xls`
- `<Name> CMA_Performance_And_Financial_Indicators.xls` *(optional but
  recommended — it lets the tool verify itself against the bank's own
  computed ratios)*

### Step 2 — Ingest

```powershell
.\venv\Scripts\python.exe cma.py ingest "D:\Work\<folder with exports>"
```

Every borrower set in the folder is pulled in at once. Watch the
reconciliation score — `reconciliation 63/63` means every engine-computed
indicator matched the bank system's own figures. Warnings about "export
total vs recomputed" mean the export carried amounts only on total rows;
the tool absorbs them and tells you where.

### Step 3 — Assess on screen

```powershell
.\venv\Scripts\python.exe cma.py assess -b "WINTAS TEXTILES"
```

One screen: Form V, key ratios, triggered red flags, RFA status, the
model ensemble, projection verdicts and a preview of the committee
queries. Omit `-b` to use the most recently ingested borrower.

### Step 4 — Generate the memorandum

```powershell
.\venv\Scripts\python.exe cma.py memo -b "WINTAS TEXTILES"
```

The Word document lands in `data\Credit_Memo_<name>.docx`. If you have
the previous version open in Word, the new one saves under a timestamped
name instead of failing.

### Step 5 — Record your registry checks

The five public portals are captcha-protected, so the look-ups stay
manual — but the tool gives you the links, remembers what you found, and
reacts to it:

```powershell
.\venv\Scripts\python.exe cma.py registry "WINTAS TEXTILES"
# ... visit the links it prints, then record each finding:
.\venv\Scripts\python.exe cma.py registry "WINTAS TEXTILES" --record gst clear "3B filed through May 2026"
.\venv\Scripts\python.exe cma.py registry "WINTAS TEXTILES" --record ibbi adverse "CIRP petition CP-123/2026"
```

What happens automatically: an **adverse** GST or EPFO finding maps to
the *Critical* statutory-dues EWS indicator — which flips the **RFA
(Red-Flagged Account)** condition on the next assessment. Registries left
unchecked (or older than 90 days) appear in the memo as a committee query.

**Probe42 reports (MCA, automated).** If you download a company report
from Probe42, the tool reads it directly — no manual typing for the MCA
leg:

```powershell
.\cma probe "D:\DL\U28999GJ2020PTC118078.pdf" -b "ARROWIN METALTECH (INDIA) PRIVATE LIMITED"
```

It extracts company status, open charges **with holder names** (so you
see who else has lent — charges held by lenders other than SBI are
flagged), auditor qualifications, legal cases, BIFR/CDR/suit-filed/
name-removal compliance, and records the MCA check automatically. An
adverse status, a qualified auditor report, or cases on record mark the
check `adverse` → into EWS and the committee queries. (Download the
report yourself from the Probe42 portal; the tool only reads the file.)

### Step 6 — News screen

```powershell
.\venv\Scripts\python.exe cma.py news                  # whole book
.\venv\Scripts\python.exe cma.py news -b "WINTAS TEXTILES"
```

Headlines are fetched from Google News (full legal name, then the
distinctive short name) and classified on your own PC by the local
model. Adverse items trigger EWS #24 and appear in memo section 5.
Zero results for a small private borrower is normal — it means "no media
footprint", and the daily sweep will catch anything that appears later.

### Step 7 — Optional: the AI committee narrative

```powershell
.\venv\Scripts\python.exe cma.py committee -b "WINTAS TEXTILES"
```

Four local AI agents (forensic analyst → liquidity analyst → underwriter
→ supervisor) draft a structured narrative, taking 1–2 minutes. Every
figure in the draft is then machine-checked against the source data;
anything untraceable is listed in the memo footer. Re-run `cma.py memo`
afterwards to include the narrative in section 8.

### Step 8 — Benchmark against your own book

Every borrower you ingest becomes a peer. Tag industries once, then:

```powershell
.\venv\Scripts\python.exe cma.py industry "WINTAS TEXTILES" "Textiles"
.\venv\Scripts\python.exe cma.py benchmark -b "WINTAS TEXTILES"
```

The borrower is placed at a percentile among same-industry peers (whole
book while an industry is thin) on margins, growth, liquidity, leverage
and DSO. Maintain sector medians from public rating-agency notes for an
external yardstick — they appear alongside automatically:

```powershell
.\venv\Scripts\python.exe cma.py norms "Textiles" --set ebitda_margin=0.075 current_ratio=1.25 tol_tnw=2.0 --source "CRISIL Apr 2026"
```

The comparison sharpens with every proposal you process — and once ten
borrower-years accumulate, the multivariate anomaly detector
(IsolationForest) activates on its own.

### The whole book

```powershell
.\venv\Scripts\python.exe cma.py portfolio
```

One line per borrower — sales, current ratio, leverage, DSCR, flag count,
model consensus, adverse news, RFA — sorted worst first.

---

## 3. Reading the memorandum

| Section | What it tells you |
|---|---|
| 1. Snapshot & Proposal | Scale, latest audited year, existing vs proposed limits |
| 2. Score Ensemble | Three independent bankruptcy models. Agreement matters, not any single model — Ohlson alone reads hot on creditor-financed Indian SMEs |
| 3. Form V (MPBF) | The working-capital math that should size the limit. A proposed limit above MPBF row 8 needs defending |
| 4. Ratios vs Thresholds | Each ratio against your sanction benchmarks, breaches marked |
| 5. Red Flags, EWS & Intelligence | The 32+13 rule results, RFA status, adverse media and registry table |
| 6. Projection Scrutiny | The borrower's projections against a statistical band fitted on their own history. AGGRESSIVE = above the 95% band — demand evidence |
| 7. Questions for the Presenting Analyst | Your interrogation sheet, priority-ordered (P1 first), each question citing the figure that prompted it |
| 8. AI Narrative | The committee draft, with attribution and the mandatory review disclaimer |
| 9. Your Remarks | Ruled lines, an Approve / Conditions / Defer / Decline tick line, signature block |
| Annexure A | Plain-language glossary — hand this to members without a credit background |

**Key verdicts to know.** Red flags: 0 = NO MATERIAL RED FLAGS, ≤3 =
MINOR CONCERNS, ≤7 = MULTIPLE CONCERNS, more = MATERIAL CREDIT CONCERNS.
Projection verdicts run IN LINE → OPTIMISTIC (above 80% band) →
AGGRESSIVE (above 95% band). The **dual column** check compares what the
borrower *estimated* for last year against what was *audited* — a big
miss is the strongest reason to discount this year's projections, and it
generates a P1 query automatically.

---

## 4. Other ways in

**Excel workbook**: open `CMA_v9_python.xlsm` (xlwings add-in pointed at
`venv\Scripts\python.exe`); the buttons run the same engines and write to
the `Python_Output` sheet (assessment in column U, EWS in column W).

**REST API**: `python cma.py serve`, then browse
`http://127.0.0.1:8000/docs`. Everything the CLI does is an endpoint —
useful if the tool is ever fronted by a dashboard.

**Template workbook route**: financials can also be keyed/pasted into
`CMA_Auto_Analytics_v9.xlsx` (sheets 0, 2, 3, 4) and ingested with
`cma.py ingest <file.xlsx>`.

---

## 5. Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `Template drift: … should contain …` | The export/workbook layout changed; the tool refuses to guess. Check the file against the expected sheet |
| `balance sheet does not balance` | The input data itself is inconsistent — fix the export, don't force it |
| `export total X vs recomputed Y` warning | The export carries an amount only on a total row, or its own subtotals disagree; the tool reconciled to the top-level totals and is telling you |
| Memo saved with a timestamp suffix | The target file was open in Word; close it or use the new copy |
| `Borrower not found` | Names must match exactly as ingested — `cma.py portfolio` lists them |
| Committee/news classification fails | Ollama isn't running — start it (`ollama serve`) or use everything else, which never needs it |
| Reconciliation below 100% | Usually a method difference (the guide's DSCR/DSO conventions were verified against the bank's own outputs); investigate only large gaps |

**Data hygiene & backup**: borrower data lives only in `db\cma.sqlite` and
`workspace\output\*.docx` on this PC — both are excluded from the code
repository. This database is your only copy of every ingested borrower,
registry/filing finding, news item, the manual index and the Probe cache.
Run **`.\cma backup`** periodically — it writes a timestamped, consistent
copy to `OneDrive\CMA_Backups\` (so it syncs off the laptop) and keeps the
latest 14. Pass `--to "<folder>"` for a different destination. To restore,
copy a `cma_*.sqlite` file back to `db\cma.sqlite`.

---

## 6. Command reference

```
cma.py ingest <folder|workbook>      pull borrower data in
cma.py assess  [-b NAME]             on-screen assessment
cma.py memo    [-b NAME]             Word memorandum -> data\
cma.py portfolio                     whole book, worst first
cma.py news    [-b NAME]             adverse-media sweep
cma.py registry NAME                 show due-diligence state + links
cma.py registry NAME --record REG STATUS [REMARKS]
                                     REG: mca gst ibbi epfo ecourts
                                     STATUS: clear adverse pending
cma.py probe REPORT.pdf -b NAME      import a downloaded Probe42 report
                                     (auto-records the MCA check)
cma.py committee [-b NAME]           4-agent AI narrative (Ollama)
cma.py benchmark [-b NAME]           borrower vs the book + industry norms
cma.py industry NAME INDUSTRY        tag a borrower's industry
cma.py norms INDUSTRY --set METRIC=VALUE ... [--source SRC]
                                     maintain sector medians
                                     metrics: ebitda_margin pat_margin
                                     sales_growth current_ratio tol_tnw dso
cma.py autorun [--no-committee]      watched folders -> credit memo (one shot)
cma.py backup [--to FOLDER]          copy the database to OneDrive (timestamped)
cma.py probe REPORT.pdf -b NAME      import a Probe42 company report (MCA check)
cma.py filing AOC4.pdf -b NAME       tie out filed financials vs the CMA
cma.py filing AUDIT.pdf -b NAME      read a scanned audit report (opinion only)
cma.py manual QUESTION...            ask the loan manual (local AI, cited)
cma.py manual --find QUESTION...     show raw manual passages instead
cma.py manual --index FILE.md        (re)index the manual markdown
cma.py serve                         REST API at 127.0.0.1:8000
cma.py demo                          synthetic end-to-end demo
```

## 7. MCA filings from Probe42 (tie-out & audit opinion)

Probe42 lets you download a company's actual MCA filings. Two are worth
running through the tool:

**Form AOC-4** (the financials as filed with MCA — a clean digital PDF):

```powershell
.\cma filing "D:\DL\Form AOC-4.pdf" -b "ARROWIN METALTECH (INDIA) PRIVATE LIMITED"
```

This *ties out* the filed revenue, PBT, PAT, net worth and receivables
against the CMA's audited column. A material mismatch on a core figure is
flagged as EWS #18 — **concealment of material facts** — which flips the
RFA condition. If everything matches (as it should for an honest file),
you get a clean confirmation you can cite in the memo.

**The audited annual report** (usually a scanned PDF). OCR reads prose
reliably but mangles digits, so the tool reads it for the **auditor's
opinion only** — never figures:

```powershell
.\cma filing "D:\DL\Annual Report Scanned.pdf" -b "ARROWIN METALTECH (INDIA) PRIVATE LIMITED"
```

It reports whether the opinion looks unmodified / qualified / adverse and
gives verification prompts. A clear qualified or adverse opinion feeds
EWS #38; statutory-dues and going-concern remain prompts for you to
confirm against the signed report. **Treat scanned-report output as
prompts, not findings** — the figures and fine print must be read by eye.

(If you have a Probe42 *API key* rather than just login, `cma probe <CIN>
-b NAME` pulls MCA master data, charges and directors directly. With only
a downloaded company report, `cma probe <report>.pdf -b NAME` imports it.)

## 8. The loan manual

Index the bank's Manual on Loans & Advances once (markdown export):

```powershell
.\cma manual --index "D:\DL\loan-manual-pdf.md"
```

Then ask it questions in plain language — answered by the local model
strictly from the manual's own text, with chapter citations, fully
offline:

```powershell
.\cma manual under second method of lending how much margin must the borrower bring
.\cma manual --find drawing power stock statement   # raw passages, no AI
```

If the extracts don't cover the question, it says so rather than
guessing. Add `--refs` to see the source passages under the answer, or
`-k 8` to retrieve more. The manual's text lives only in your local
database — it is never committed to the code repository.
