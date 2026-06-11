"""
4-Agent Credit Committee — Transparent Multi-Agent Narrative Layer
Agents:
  1. Supervisor      : orchestrates, routes tasks, assembles final state
  2. Forensic        : analyses anomaly outputs, flags data integrity issues
  3. Liquidity       : analyses forecast + DSCR, assesses short-term solvency
  4. Underwriter     : synthesises all inputs, writes final credit memo

All agents run locally via Ollama. No cloud APIs.
Every output includes explicit attribution: which agent, which model, what prompt.
Final memo carries mandatory AI-drafted disclaimer.
"""
import hashlib
import pathlib
import sqlite3
from dataclasses import dataclass, field
from typing import Optional
from ollama import Client

ROOT   = pathlib.Path(__file__).resolve().parents[2]
DB     = ROOT / "db" / "cma.sqlite"
OLLAMA = Client(host="http://localhost:11434")

# ── Model assignments ─────────────────────────────────────────────────────────
MODELS = {
    "supervisor":   "llama3.2:3b",       # fast, lightweight orchestrator
    "forensic":     "qwen2.5:7b",        # strong at structured data analysis
    "liquidity":    "qwen2.5:7b",        # strong at structured data analysis
    "underwriter":  "llama3.1:8b",       # best narrative quality
}

# ── System prompts — accuracy focused, NO style manipulation ──────────────────
PROMPTS = {
    "forensic": """You are a forensic credit analyst at an Indian bank.
Read the anomaly detection outputs provided and write 3-5 bullet points.
Rules:
- State ONLY what the numbers show. No speculation.
- Cite the metric name beside each statement.
- Use plain financial language. No marketing terms.
- Be terse. Each bullet maximum 20 words.
- If data is insufficient, say so explicitly.""",

    "liquidity": """You are a liquidity analyst at an Indian bank.
Read the forecast and DSCR data provided and write 4-6 bullet points.
Rules:
- Focus strictly on: (1) coverage, (2) cushion vs downside, (3) refinancing risk.
- Quote actual numbers in every bullet.
- Flag any week where cash drops below zero.
- Be terse. Each bullet maximum 25 words.
- If weekly data is unavailable, say so and focus on annual metrics only.""",

    "underwriter": """You are a senior underwriting executive at an Indian scheduled commercial bank.
You will receive structured findings from a Forensic Analyst and a Liquidity Analyst.
Write a Credit Justification Memorandum with exactly this structure:

1. BORROWER SNAPSHOT (1 sentence: name, industry, proposal type)
2. QUANTITATIVE RISK (Ohlson PD%, key ratios — 3-4 sentences)
3. FORENSIC FINDINGS (reproduce the forensic bullets verbatim)
4. LIQUIDITY ASSESSMENT (reproduce the liquidity bullets verbatim)
5. RECOMMENDATION (one of: APPROVE / APPROVE WITH COVENANTS / DECLINE / REFER TO CREDIT COMMITTEE)
   Justify the recommendation in 2-3 sentences citing specific numbers.

End the memo with this exact line:
AI-DRAFTED MEMO — REQUIRES ANALYST REVIEW AND SIGN-OFF BEFORE SUBMISSION.

Rules:
- Preserve every number exactly as provided. Do not invent figures.
- Formal tone. No filler phrases. No marketing language.
- Total length: 250-350 words.""",

    "supervisor": """You are a workflow supervisor.
Given the completed credit memo, output ONLY valid JSON with these exact keys:
{
  "verdict": "APPROVE" or "APPROVE WITH COVENANTS" or "DECLINE" or "REFER",
  "confidence": "LOW" or "MEDIUM" or "HIGH",
  "key_risk": "one sentence describing the single biggest risk",
  "needs_review": true or false
}
Output nothing else. No explanation. No markdown. Pure JSON only.""",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AgentOutput:
    agent:       str
    model:       str
    prompt_hash: str
    output:      str
    error:       str = ""


@dataclass
class CommitteeResult:
    borrower_name:  str
    ohlson_pd:      float
    ohlson_band:    str
    forensic:       Optional[AgentOutput] = None
    liquidity:      Optional[AgentOutput] = None
    underwriter:    Optional[AgentOutput] = None
    supervisor:     Optional[AgentOutput] = None
    final_memo:     str = ""
    verdict:        str = ""
    confidence:     str = ""
    key_risk:       str = ""
    audit_log:      list = field(default_factory=list)
    error:          str = ""


# ── Helper functions ──────────────────────────────────────────────────────────

def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def _call(agent_name, user_content, temperature=0.2):
    """Call an Ollama model and return AgentOutput."""
    model      = MODELS[agent_name]
    system     = PROMPTS[agent_name]
    prompt_key = _hash(system + user_content)

    try:
        response = OLLAMA.chat(
            model=model,
            messages=[
                {"role": "system",  "content": system},
                {"role": "user",    "content": user_content},
            ],
            options={"temperature": temperature, "num_ctx": 4096},
        )
        return AgentOutput(
            agent=agent_name,
            model=model,
            prompt_hash=prompt_key,
            output=response["message"]["content"],
        )
    except Exception as e:
        return AgentOutput(
            agent=agent_name,
            model=model,
            prompt_hash=prompt_key,
            output="",
            error=str(e),
        )


def _save_narrative(result):
    """Persist memo and audit log to SQLite."""
    try:
        with sqlite3.connect(DB) as conn:
            borrower = conn.execute(
                "SELECT borrower_id FROM borrower WHERE name = ?",
                (result.borrower_name,)
            ).fetchone()

            if not borrower:
                return

            bid = borrower[0]

            # Save each agent output
            for ao in [result.forensic, result.liquidity,
                       result.underwriter, result.supervisor]:
                if ao and ao.output:
                    conn.execute("""
                        INSERT INTO narrative
                        (borrower_id, agent, model, prompt_hash, output_text)
                        VALUES (?, ?, ?, ?, ?)
                    """, (bid, ao.agent, ao.model,
                          ao.prompt_hash, ao.output))
    except Exception:
        pass   # DB save failure should not block the memo


# ── Agent nodes ───────────────────────────────────────────────────────────────

def _run_forensic(anomaly_data):
    """Forensic Analyst: analyses anomaly outputs."""
    user = f"""ANOMALY DETECTION RESULTS:

Beneish M-Score: {anomaly_data.get('beneish_score', 'not run')}
Beneish Verdict: {anomaly_data.get('beneish_verdict', 'not run')}

Rolling Z-Scores:
{anomaly_data.get('z_scores_text', 'No Z-score data available')}

IsolationForest: {anomaly_data.get('isoforest_verdict', 'not run')}
Master Verdict:  {anomaly_data.get('master_verdict', 'not assessed')}

Analyse these results and provide your forensic assessment."""

    return _call("forensic", user)


def _run_liquidity(forecast_data, dscr_data):
    """Liquidity Agent: analyses forecast and DSCR."""
    user = f"""FORECAST AND LIQUIDITY DATA:

Annual Forecasts (ETS model):
{forecast_data.get('annual_text', 'No annual forecast data')}

DSCR (latest audited year): {dscr_data.get('dscr_latest', 'not available')}x
Cascade stress test (Recession, P5): {dscr_data.get('cascade_p5', 'not run')}x
Prob DSCR below 1.0 under stress: {dscr_data.get('prob_failure', 'not run')}

Weekly cash flow:
{forecast_data.get('weekly_text', 'No weekly cash flow data available')}

Provide your liquidity assessment."""

    return _call("liquidity", user)


def _run_underwriter(borrower_data, forensic_out, liquidity_out):
    """Underwriting Executive: synthesises final memo."""
    user = f"""BORROWER: {borrower_data.get('name', 'Unknown')}
INDUSTRY: {borrower_data.get('industry', 'Unknown')}
PROPOSAL: {borrower_data.get('proposal', 'Not specified')}

OHLSON PROBABILITY OF DEFAULT: {borrower_data.get('ohlson_pd', 0):.1%}
OHLSON RISK BAND: {borrower_data.get('ohlson_band', 'Unknown')}

FORENSIC ANALYST FINDINGS:
{forensic_out.output if forensic_out and not forensic_out.error else 'Forensic analysis unavailable'}

LIQUIDITY ANALYST FINDINGS:
{liquidity_out.output if liquidity_out and not liquidity_out.error else 'Liquidity analysis unavailable'}

Write the Credit Justification Memorandum."""

    return _call("underwriter", user, temperature=0.3)


def _run_supervisor(memo_text):
    """Supervisor: classifies the verdict as JSON."""
    return _call("supervisor", memo_text, temperature=0.0)


# ── Master runner ─────────────────────────────────────────────────────────────

def run_committee(
    borrower_name=None,
    anomaly_data=None,
    forecast_data=None,
    dscr_data=None,
):
    """
    Run the full 4-agent credit committee.
    Returns CommitteeResult with final memo and audit log.
    """
    # Load borrower data from SQLite if not provided
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        if borrower_name:
            borrower = conn.execute(
                "SELECT * FROM borrower WHERE name = ?",
                (borrower_name,)
            ).fetchone()
        else:
            borrower = conn.execute(
                "SELECT * FROM borrower ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not borrower:
            return CommitteeResult(
                borrower_name="NOT FOUND",
                ohlson_pd=0,
                ohlson_band="UNKNOWN",
                error="No borrower found in database",
            )

        # Get latest Ohlson result from DB
        ohlson_row = conn.execute("""
            SELECT score, notes FROM anomaly_result
            WHERE borrower_id = ? AND detector = 'ohlson_pd'
            ORDER BY run_ts DESC LIMIT 1
        """, (borrower["borrower_id"],)).fetchone()

        # Get latest Beneish result
        beneish_row = conn.execute("""
            SELECT score, notes FROM anomaly_result
            WHERE borrower_id = ? AND detector = 'beneish_m'
            ORDER BY run_ts DESC LIMIT 1
        """, (borrower["borrower_id"],)).fetchone()

    ohlson_pd   = float(ohlson_row["score"]) if ohlson_row else 0.0
    ohlson_band = "SEVERE" if ohlson_pd >= 0.5 else \
                  "ELEVATED" if ohlson_pd >= 0.2 else \
                  "WATCH"    if ohlson_pd >= 0.038 else "LOW"

    result = CommitteeResult(
        borrower_name=borrower["name"],
        ohlson_pd=ohlson_pd,
        ohlson_band=ohlson_band,
    )

    # Build data packages for agents
    if anomaly_data is None:
        anomaly_data = {
            "beneish_score":   beneish_row["score"] if beneish_row else "not run",
            "beneish_verdict": beneish_row["notes"] if beneish_row else "not run",
            "z_scores_text":   "Run anomaly module to populate Z-scores",
            "isoforest_verdict": "Insufficient portfolio data",
            "master_verdict":  "Partial data — run full anomaly suite",
        }

    if forecast_data is None:
        forecast_data = {
            "annual_text": "Run forecast module to populate annual projections",
            "weekly_text": "No weekly cash flow data loaded",
        }

    if dscr_data is None:
        dscr_data = {
            "dscr_latest":  "not available",
            "cascade_p5":   "not run",
            "prob_failure": "not run",
        }

    borrower_data = {
        "name":       borrower["name"],
        "industry":   borrower["industry"] or "Not specified",
        "proposal":   borrower["rbi_sector"] or "Not specified",
        "ohlson_pd":  ohlson_pd,
        "ohlson_band": ohlson_band,
    }

    # ── Run the 4 agents in sequence ──────────────────────────────────────────
    print(f"  [1/4] Forensic Analyst ({MODELS['forensic']})...")
    result.forensic = _run_forensic(anomaly_data)
    result.audit_log.append({
        "agent": "forensic",
        "model": result.forensic.model,
        "prompt_hash": result.forensic.prompt_hash,
        "error": result.forensic.error,
    })

    print(f"  [2/4] Liquidity Agent ({MODELS['liquidity']})...")
    result.liquidity = _run_liquidity(forecast_data, dscr_data)
    result.audit_log.append({
        "agent": "liquidity",
        "model": result.liquidity.model,
        "prompt_hash": result.liquidity.prompt_hash,
        "error": result.liquidity.error,
    })

    print(f"  [3/4] Underwriting Executive ({MODELS['underwriter']})...")
    result.underwriter = _run_underwriter(
        borrower_data, result.forensic, result.liquidity
    )
    result.audit_log.append({
        "agent": "underwriter",
        "model": result.underwriter.model,
        "prompt_hash": result.underwriter.prompt_hash,
        "error": result.underwriter.error,
    })

    # Add attribution footer
    if result.underwriter and result.underwriter.output:
        result.final_memo = (
            result.underwriter.output
            + "\n\n---\n"
            + f"Generated by: Forensic={MODELS['forensic']} | "
            + f"Liquidity={MODELS['liquidity']} | "
            + f"Underwriter={MODELS['underwriter']}\n"
            + f"Prompt hashes: F={result.forensic.prompt_hash} "
            + f"L={result.liquidity.prompt_hash} "
            + f"U={result.underwriter.prompt_hash}"
        )

    print(f"  [4/4] Supervisor ({MODELS['supervisor']})...")
    result.supervisor = _run_supervisor(result.final_memo)
    result.audit_log.append({
        "agent": "supervisor",
        "model": result.supervisor.model,
        "prompt_hash": result.supervisor.prompt_hash,
        "error": result.supervisor.error,
    })

    # Parse supervisor JSON
    try:
        import json
        import re
        sup_text = result.supervisor.output.strip()

        # Strip markdown code fences if present
        sup_text = re.sub(r"```(?:json)?", "", sup_text).strip()

        # Extract first JSON object found in the output
        match = re.search(r'\{.*?\}', sup_text, re.DOTALL)
        if match:
            sup_text = match.group(0)

        sup_json = json.loads(sup_text)
        result.verdict     = sup_json.get("verdict", "REFER")
        result.confidence  = sup_json.get("confidence", "LOW")
        result.key_risk    = sup_json.get("key_risk", "")
    except Exception:
        result.verdict    = "REFER"
        result.confidence = "LOW"
        result.key_risk   = "Supervisor JSON parse failed — manual review required"

    # Save to DB
    _save_narrative(result)

    return result