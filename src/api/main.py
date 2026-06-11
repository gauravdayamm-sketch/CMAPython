"""
CMA Local API Server
Runs on http://127.0.0.1:8000
All endpoints are localhost-only — no external exposure.

Endpoints:
  GET  /              health check
  POST /ohlson        run Ohlson O-Score
  POST /anomaly       run anomaly detection
  POST /cascade       run causal cascade + Monte Carlo
  POST /forecast      run ETS forecast
  POST /narrative     run 4-agent credit committee (background)
  GET  /narrative/{job_id}  check narrative job status
  POST /feeds         fetch RBI + market data
  GET  /rbi           get cached RBI notifications
  GET  /docs          auto-generated API documentation
"""
import uuid
import sys
import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analytics.ohlson  import compute_ohlson_from_dict
from analytics.anomaly import run_all_anomaly, save_anomaly_to_db
from analytics.cascade import run_cascade_from_dict, SCENARIOS
from analytics.forecast import run_forecast_summary, scrutinize_projections
from analytics.wc_assessment import assess_working_capital, form_v
from agents.committee  import run_committee
from data.feeds        import (
    fetch_rbi_updates, save_rbi_to_db,
    get_rbi_cached, daily_refresh, start_scheduler
)
from ingest.cma_workbook import ingest_workbook


# ── Job store (in-memory) ─────────────────────────────────────────────────────
JOBS: dict = {}
MAX_JOBS = 100


def _evict_finished_jobs():
    """Keep the job store bounded: drop oldest finished jobs beyond the cap."""
    if len(JOBS) <= MAX_JOBS:
        return
    for jid in list(JOBS):
        if len(JOBS) <= MAX_JOBS:
            break
        if JOBS[jid].get("status") != "running":
            del JOBS[jid]


# ── Lifespan: start scheduler on startup ─────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CMA Local Analytics API",
    description="Local REST API for CMA Auto-Analytics Python backend",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Request models ────────────────────────────────────────────────────────────

class OhlsonRequest(BaseModel):
    total_assets:        float
    total_liabilities:   float
    current_assets:      float
    current_liabilities: float
    working_capital:     float
    net_income:          float
    ffo:                 float
    net_income_prev:     Optional[float] = None

class CascadeRequest(BaseModel):
    sales:         float
    opm:           float
    ebitda:        float
    dso:           float
    inv_days:      float
    depreciation:  float
    interest:      float
    tl_interest:   float
    tl_instalment: float
    stbb:          float
    total_debt:    float
    cash_accruals: float
    scenario:      str = "Recession"
    min_dscr:      float = 1.25

class NarrativeRequest(BaseModel):
    borrower_name: Optional[str] = None

class FeedsRequest(BaseModel):
    since_days: int = 30

class CMAIngestRequest(BaseModel):
    path: str

class EWSRequest(BaseModel):
    borrower_name: Optional[str] = None
    manual_triggers: list[int] = []

class MemoRequest(BaseModel):
    borrower_name: Optional[str] = None
    out_path: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "CMA Local Analytics API",
        "version": "1.0.0",
    }


@app.post("/ohlson")
def run_ohlson(req: OhlsonRequest):
    """Run Ohlson O-Score probability of default model."""
    result = compute_ohlson_from_dict(req.model_dump())
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/anomaly")
def run_anomaly(borrower_name: Optional[str] = None):
    """Run all 3 anomaly detection engines."""
    report = run_all_anomaly(borrower_name)
    save_anomaly_to_db(report)
    return {
        "borrower":       report.borrower_name,
        "fy_end":         report.fy_end,
        "master_verdict": report.master_verdict,
        "beneish": {
            "m_score": report.beneish.m_score if report.beneish else None,
            "verdict": report.beneish.verdict if report.beneish else None,
        } if report.beneish else None,
        "z_scores": [
            {
                "metric":  z.metric,
                "z_score": z.z_score,
                "verdict": z.verdict,
            }
            for z in report.z_scores
        ],
        "isoforest": {
            "score":   report.isoforest.score if report.isoforest else None,
            "verdict": report.isoforest.verdict if report.isoforest else None,
        } if report.isoforest else None,
    }


@app.post("/cascade")
def run_cascade(req: CascadeRequest):
    """Run causal cascade with 10,000 Monte Carlo draws."""
    if req.scenario not in SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario. Choose from: {SCENARIOS}"
        )
    result = run_cascade_from_dict(
        req.model_dump(exclude={"scenario", "min_dscr"}),
        scenario_name=req.scenario,
        min_dscr=req.min_dscr,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/forecast")
def run_forecast_endpoint(borrower_name: Optional[str] = None):
    """Run ETS annual forecast for a borrower."""
    summary = run_forecast_summary(borrower_name)
    return summary


@app.post("/narrative")
def run_narrative_endpoint(
    req: NarrativeRequest,
    bg: BackgroundTasks,
):
    """
    Start 4-agent credit committee as a background task.
    Returns job_id immediately. Poll GET /narrative/{job_id} for result.
    """
    job_id = str(uuid.uuid4())
    _evict_finished_jobs()
    JOBS[job_id] = {"status": "running", "memo": None, "error": None}

    def _run():
        try:
            result = run_committee(borrower_name=req.borrower_name)
            JOBS[job_id] = {
                "status":       "done",
                "borrower":     result.borrower_name,
                "verdict":      result.verdict,
                "confidence":   result.confidence,
                "key_risk":     result.key_risk,
                "memo":         result.final_memo,
                "audit_log":    result.audit_log,
                "number_audit": result.number_audit,
                "error":        result.error,
            }
        except Exception as e:
            JOBS[job_id] = {
                "status": "error",
                "error":  str(e),
            }

    bg.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@app.get("/narrative/{job_id}")
def get_narrative(job_id: str):
    """Poll for narrative generation result."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id]


@app.post("/feeds")
def run_feeds_endpoint(req: FeedsRequest):
    """Fetch and cache RBI notifications."""
    items, err = fetch_rbi_updates("notifications", since_days=req.since_days)
    if err:
        raise HTTPException(status_code=503, detail=f"RBI feed error: {err}")
    saved = save_rbi_to_db(items)
    return {
        "fetched": len(items),
        "saved":   saved,
        "items":   items[:5],
    }


@app.get("/rbi")
def get_rbi(limit: int = 10):
    """Get cached RBI notifications from SQLite."""
    items = get_rbi_cached(limit=limit)
    return {"count": len(items), "items": items}


@app.post("/refresh")
def manual_refresh():
    """Trigger manual data refresh (runs synchronously)."""
    daily_refresh()
    return {"status": "refresh complete"}


# ── CMA pipeline ──────────────────────────────────────────────────────────────

@app.post("/cma/ingest")
def cma_ingest(req: CMAIngestRequest):
    """Parse a saved CMA workbook and persist it to SQLite."""
    import pathlib
    if not pathlib.Path(req.path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")
    data, bid = ingest_workbook(req.path)
    if data.errors():
        raise HTTPException(status_code=422, detail={
            "message": "Ingestion failed validation",
            "errors": data.errors(),
        })
    return {
        "borrower":    data.borrower["name"],
        "borrower_id": bid,
        "years":       [{"fy_label": y.fy_label,
                         "statement_type": y.statement_type,
                         "is_dual": y.is_dual} for y in data.years],
        "warnings":    [m for lvl, m in data.issues if lvl == "WARNING"],
    }


@app.get("/cma/assessment")
def cma_assessment(borrower_name: Optional[str] = None):
    """Form V MPBF, DSCR, ratios, threshold verdicts and red flags."""
    a = assess_working_capital(borrower_name)
    if a.error:
        raise HTTPException(status_code=404, detail=a.error)
    return {
        "borrower":       a.borrower_name,
        "latest_audited": a.latest_audited["fy_label"] if a.latest_audited else None,
        "form_v":         [{"item": label, "value": value}
                           for label, value in form_v(a)],
        "dscr_avg_gross": a.dscr_avg_gross,
        "years": [{"fy_label": y["fy_label"],
                   "statement_type": y["statement_type"],
                   "is_dual": y["is_dual"],
                   "metrics": y["metrics"]} for y in a.years],
        "verdicts":  a.verdicts,
        "red_flags": a.red_flags,
        "proposals": a.proposals,
    }


@app.get("/cma/projections")
def cma_projections(borrower_name: Optional[str] = None):
    """Borrower's CMA projections tested against ETS forecast bands."""
    result = scrutinize_projections(borrower_name)
    if result["error"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/cma/memo")
def cma_memo(req: MemoRequest):
    """Export the full credit memorandum as a Word document."""
    from report.memo_docx import generate_credit_memo
    try:
        path = generate_credit_memo(req.borrower_name, out_path=req.out_path)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"path": str(path)}


@app.get("/scores")
def scores(borrower_name: Optional[str] = None):
    """Ohlson + Altman Z″ + Zmijewski distress ensemble."""
    from analytics.scores import run_ensemble
    result = run_ensemble(borrower_name)
    if result.error:
        raise HTTPException(status_code=404, detail=result.error)
    return result.as_dict()


@app.post("/cma/ews")
def cma_ews(req: EWSRequest):
    """32 red flags + 13 exception tests + RBI EWS indicators with RFA rule.

    manual_triggers: catalogue ids of qualitative EWS indicators observed
    by the desk officer (e.g. 19 = forensic audit qualification).
    """
    from analytics.ews import run_full_ews
    r = run_full_ews(req.borrower_name, manual_triggers=req.manual_triggers)
    if r.error:
        raise HTTPException(status_code=404, detail=r.error)
    return {
        "borrower":         r.borrower_name,
        "red_flag_verdict": r.red_flag_verdict,
        "rfa_required":     r.rfa_required,
        "rfa_reason":       r.rfa_reason,
        "red_flags":   [vars(f) | {"triggered": f.triggered} for f in r.red_flags],
        "exceptions":  [vars(f) | {"triggered": f.triggered} for f in r.exceptions],
        "ews_indicators": r.ews_indicators,
    }