# CMA Python — Credit Monitoring & Analytics

Local-first credit analytics backend for CMA (Credit Monitoring Arrangement)
workflows at Indian banks. Python does the math; Excel stays the front end;
LLM narrative generation runs fully offline via Ollama. No cloud APIs.

**Start here:** [docs/GUIDE.md](docs/GUIDE.md) — the desk officer's manual
(also as a Word document in `docs/`). Everything is driven from one command:

```powershell
.\venv\Scripts\python.exe cma.py ingest "D:\path\to\LLMS exports"
.\venv\Scripts\python.exe cma.py assess
.\venv\Scripts\python.exe cma.py memo
.\venv\Scripts\python.exe cma.py portfolio
```

## Architecture

```
Excel (CMA_v9_python.xlsm)
   │  xlwings RunPython
   ▼
src/bridge/excel_io.py ──► writes results to the Python_Output sheet
   │
   ├─ src/ingest/cma_workbook.py      CMA form ingestion (OS/BS/DSCR sheets → SQLite)
   ├─ src/analytics/wc_assessment.py  Form V MPBF (Tandon I/II), DSCR, ratios, red flags
   ├─ src/analytics/ohlson.py         Ohlson O-Score probability of default
   ├─ src/analytics/anomaly.py        Beneish M-Score, rolling Z-scores, IsolationForest
   ├─ src/analytics/cascade.py        Causal stress cascade + 10,000-draw Monte Carlo DSCR
   ├─ src/analytics/forecast.py       ETS annual/weekly forecasts + projection scrutiny
   ├─ src/agents/committee.py         4-agent credit committee (local Ollama models)
   └─ src/data/feeds.py               RBI RSS notifications + NSE quotes, cached in SQLite
   │
   ▼
db/cma.sqlite  (borrowers, financials, CMA statements/lines, results, caches)

src/api/main.py  ──  FastAPI wrapper around the same modules (localhost:8000)
```

## The CMA pipeline

The workbook's `0_Setup`, `2_Operating_Statement`, `3_Balance_Sheet` and
`4_DSCR` sheets are parsed straight into SQLite (`cma_statement` /
`cma_line`). Only input cells are read; every subtotal is recomputed in
Python and cross-validated (balance sheet must balance to ±0.5 Cr, template
drift is detected via row anchors). The year architecture mirrors the
workbook: audited history, a dual Estimated/Audited pair for the prior FY
(their variance = projection credibility), the current-year estimate, then
projections.

On top of the ingested data:

- **Form V working capital assessment** — MPBF under Tandon Method I and II,
  ABF, Nayak turnover-method comparison, using the borrower's own 0_Setup
  thresholds.
- **DSCR** — gross/net per year, average over term-debt years.
- **Ratios + red flags** — current/quick ratio, TOL/TNW, Debt/EBITDA, ICR,
  FACR, holding levels, cash conversion cycle; NWC erosion, DSO/inventory
  drift, sales-floor and estimate-vs-actual credibility flags.
- **Projection scrutiny** — the borrower's projected Sales/EBITDA/PAT tested
  against ETS bands fitted on their audited history (log-space fit, so a
  steady-CAGR history is judged fairly).
- **EWS / Red Flags engine** (`analytics/ews.py`) — the workbook's 32
  quantitative red flags and 13 deep-diagnostic exception tests, fully
  recomputed from ingested data, plus the RBI Fraud Risk Master Direction
  EWS indicators: financial-behavior signals auto-detected, qualitative
  ones accepted as manual triggers, and the RFA rule applied (any Critical
  or 2+ High severity → flag for Red Flagged Account).
- **Distress score ensemble** (`analytics/scores.py`) — Ohlson O-Score,
  Altman Z″ (1995 EM) and Zmijewski X side by side with reason codes; no
  single 1980s US-calibrated model decides an Indian file, cross-model
  agreement is the signal. Feeds the underwriter agent automatically.
- **Memo number audit** — after the committee drafts a memo, a
  deterministic (non-LLM) verifier traces every figure in it back to the
  source data given to the agents; untraceable numbers are listed in the
  memo footer for the analyst. The supervisor's verdict JSON is enforced
  via Ollama's JSON mode, and the EWS findings flow into the forensic
  agent's brief.
- **Word export** (`report/memo_docx.py`) — the full credit memorandum
  (snapshot, ensemble, Form V, ratios, EWS, projection scrutiny, committee
  queries, AI narrative with disclaimer) as a .docx, recomputed from the
  database at export time. `POST /cma/memo` or part of the console demo.
- **Committee queries** (`report/committee_queries.py`) — every breached
  parameter, red flag, optimistic projection, estimate-vs-audit miss and
  over-MPBF limit request generates the pointed questions a sanctioning
  authority should put to the presenting analyst, with the triggering
  figures cited. Deterministic (template-driven), priority-ordered.
  `GET /cma/queries`, plus memo section 7.
- **Market & registry intelligence** (`data/news_intel.py`,
  `data/registry_checks.py`) — Google News headlines per borrower,
  classified locally by Ollama (keyword screen as the floor), cached and
  swept daily; adverse media auto-triggers EWS #24. Public-registry due
  diligence (MCA / GST / IBBI / EPFO / e-courts) is recorded with portal
  deep links — an adverse GST/EPFO finding maps to the Critical
  statutory-dues EWS (→ RFA), and unchecked or stale registries surface
  as committee queries. `POST /intel/news`, `GET|POST /intel/registry`.

**Two ways in for borrower data:**

1. The CMA workbook itself (`ingest/cma_workbook.py`).
2. **LLMS system exports** (`ingest/llms_export.py`) — the per-statement
   Balance_Sheet / Operating_Statement / Performance .xls or .csv files the
   bank system produces. Rows are matched by label within section (robust to
   row drift), amounts carried only on total rows are absorbed with an audit
   note, top-level totals defer to the export, and debt service uses the
   prior column's "instalments due within 1 year" — conventions verified to
   **99%+ reconciliation against the bank's own Performance-file indicators**
   on real borrower exports. Batch-run a folder:

   ```powershell
   .\venv\Scripts\python.exe scripts\run_llms_pipeline.py "D:\path\to\exports"
   ```

Run it from Excel (`run_cma_assessment` writes to Python_Output col U),
the API (`POST /cma/ingest`, `POST /cma/ingest-llms`, `GET /cma/assessment`,
`GET /cma/projections`), or the console demo:

```powershell
.\venv\Scripts\python.exe src\test_cma.py   # generates a synthetic demo if
                                            # the template is unfilled
```

## Setup

```powershell
# 1. Virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Create the database and seed a test borrower
python scripts\init_db.py
python src\setup_test_borrower.py

# 3. (For the credit committee) install Ollama and pull the models
ollama pull llama3.2:3b
ollama pull qwen2.5:7b
ollama pull llama3.1:8b
```

## Running

**API server** (also starts Ollama if needed):

```powershell
.\start.ps1
# or manually:
.\venv\Scripts\python.exe -m uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Endpoints (see `http://127.0.0.1:8000/docs`):

| Method | Path                  | Purpose                                   |
|--------|-----------------------|-------------------------------------------|
| GET    | `/`                   | health check                              |
| POST   | `/ohlson`             | Ohlson O-Score from posted financials     |
| POST   | `/anomaly`            | run all 3 anomaly engines                 |
| POST   | `/cascade`            | stress cascade + Monte Carlo              |
| POST   | `/forecast`           | ETS annual + weekly forecast              |
| POST   | `/narrative`          | start 4-agent committee (background job)  |
| GET    | `/narrative/{job_id}` | poll committee result                     |
| POST   | `/feeds`              | fetch + cache RBI notifications           |
| GET    | `/rbi`                | cached RBI notifications                  |
| POST   | `/cma/ingest`         | parse a CMA workbook into SQLite          |
| GET    | `/cma/assessment`     | Form V MPBF, DSCR, ratios, red flags      |
| GET    | `/cma/projections`    | borrower projections vs ETS bands         |
| POST   | `/cma/ews`            | red flags + exception tests + RBI EWS/RFA |
| GET    | `/scores`             | Ohlson + Altman Z″ + Zmijewski ensemble   |
| POST   | `/cma/memo`           | export credit memorandum as .docx         |

**Excel bridge**: open `CMA_v9_python.xlsm` (xlwings add-in required, with the
interpreter pointed at `venv\Scripts\python.exe`). The VBA buttons call
`bridge.excel_io` functions, which write to the `Python_Output` sheet.

**Demo scripts** (console walkthroughs of each module):

```powershell
.\venv\Scripts\python.exe src\test_cma.py         # full CMA pipeline demo
.\venv\Scripts\python.exe src\test_ohlson.py      # pure-math demo, no DB needed
.\venv\Scripts\python.exe src\test_anomaly.py     # needs seeded DB
.\venv\Scripts\python.exe src\test_cascade.py
.\venv\Scripts\python.exe src\test_forecast.py
.\venv\Scripts\python.exe src\test_feeds.py       # live RBI/NSE network calls
.\venv\Scripts\python.exe src\test_committee.py   # needs Ollama running (60–120 s)
```

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest
```

The pytest suite lives in `tests/` and uses temporary databases and mocked
network/LLM calls — it does not touch `db/cma.sqlite`, the internet, or
Ollama. The CMA tests fill a copy of the real template with a synthetic
borrower, so the parser is exercised against the genuine sheet layout.

## Notes

- `db/cma.sqlite` is runtime data and not committed; recreate it any time with
  `scripts/init_db.py` (idempotent — also migrates older databases).
- The committee memo is AI-drafted and carries a mandatory
  "requires analyst review" disclaimer; agent/model/prompt-hash attribution is
  stored in the `narrative` table for auditability.
- `SIZE_DEFLATOR` in `ohlson.py` (median Indian corporate total assets, ₹ Cr)
  should be refreshed periodically.
