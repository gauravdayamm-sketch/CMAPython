"""
Causal Cascade Engine + Monte Carlo Simulation
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

SCENARIOS = [
    "Brent Oil Shock",
    "Supply Chain Disruption",
    "Rate Hike 200bps",
    "Demand Slump",
    "INR Depreciation",
    "Recession",
]

ELASTICITY = np.array([
    [ -0.05,  -0.04,  20,  15,  50  ],
    [ -0.10,  -0.04,  25,  30,  25  ],
    [ -0.05,  -0.01,  10,   5,  200 ],
    [ -0.20,  -0.02,  30,  25,  50  ],
    [ +0.02,  -0.025, 10,  10,  100 ],
    [ -0.25,  -0.05,  40,  20,  75  ],
])


@dataclass
class CascadeInputs:
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


@dataclass
class CascadePoint:
    severity:               float
    stressed_sales:         float
    stressed_opm:           float
    stressed_ebitda:        float
    delta_dso:              float
    delta_inv:              float
    wc_gap:                 float
    stressed_interest:      float
    stressed_cash_accruals: float
    stressed_dscr:          float
    verdict:                str


@dataclass
class MonteCarloResult:
    scenario:            str
    n_sims:              int
    dscr_mean:           float
    dscr_p5:             float
    dscr_p25:            float
    dscr_p50:            float
    dscr_p75:            float
    dscr_p95:            float
    prob_below_1:        float
    prob_below_covenant: float
    breakeven_severity:  Optional[float]
    ladder:              list = field(default_factory=list)
    verdict:             str  = ""


def _cascade_at_severity(inputs, scenario_idx, severity, min_dscr=1.25):
    e = ELASTICITY[scenario_idx]

    s_sales  = inputs.sales * (1 + e[0] * severity)
    s_opm    = inputs.opm   +  e[1] * severity
    s_ebitda = s_sales * s_opm

    d_dso = e[2] * severity
    d_inv = e[3] * severity
    wc_gap = (d_dso + d_inv) * s_sales / 365

    base_rate  = (inputs.interest / inputs.total_debt
                  if inputs.total_debt > 0 else 0)
    int_bps    = e[4] / 10000
    s_rate     = base_rate + int_bps * severity
    s_interest = (inputs.total_debt + wc_gap) * s_rate
    s_tl_int   = inputs.tl_interest + inputs.total_debt * int_bps * severity

    s_pbt     = s_ebitda - inputs.depreciation - s_interest
    s_cash_acc = max(0, s_pbt) * 0.75 + inputs.depreciation

    denom  = inputs.tl_instalment + s_tl_int
    s_dscr = (s_cash_acc + s_tl_int) / denom if denom > 0 else 0

    if s_dscr < 1.0:
        verdict = "DEBT SERVICE FAILURE"
    elif s_dscr < min_dscr:
        verdict = "COVENANT BREACH"
    elif s_dscr < 1.5:
        verdict = "TIGHT"
    else:
        verdict = "RESILIENT"

    return CascadePoint(
        severity=severity,
        stressed_sales=round(s_sales, 2),
        stressed_opm=round(s_opm, 4),
        stressed_ebitda=round(s_ebitda, 2),
        delta_dso=round(d_dso, 1),
        delta_inv=round(d_inv, 1),
        wc_gap=round(wc_gap, 2),
        stressed_interest=round(s_interest, 2),
        stressed_cash_accruals=round(s_cash_acc, 2),
        stressed_dscr=round(s_dscr, 4),
        verdict=verdict,
    )


def run_severity_ladder(inputs, scenario_idx, min_dscr=1.25):
    levels = [0.0, 0.25, 0.50, 0.75, 1.00, 1.50]
    return [
        _cascade_at_severity(inputs, scenario_idx, sev, min_dscr)
        for sev in levels
    ]


def find_breakeven(inputs, scenario_idx, min_dscr=1.25, precision=0.01):
    worst = _cascade_at_severity(inputs, scenario_idx, 2.0, min_dscr)
    if worst.stressed_dscr >= 1.0:
        return None

    base = _cascade_at_severity(inputs, scenario_idx, 0.0, min_dscr)
    if base.stressed_dscr < 1.0:
        return 0.0

    lo, hi = 0.0, 2.0
    for _ in range(50):
        mid = (lo + hi) / 2
        pt  = _cascade_at_severity(inputs, scenario_idx, mid, min_dscr)
        if pt.stressed_dscr < 1.0:
            hi = mid
        else:
            lo = mid
        if hi - lo < precision:
            break

    return round((lo + hi) / 2, 2)


def run_monte_carlo(
    inputs,
    scenario_name,
    min_dscr=1.25,
    n_sims=10_000,
    severity_mean=0.5,
    severity_std=0.3,
    seed=42,
):
    scenario_idx = SCENARIOS.index(scenario_name)
    e = ELASTICITY[scenario_idx]

    rng     = np.random.default_rng(seed)
    raw     = rng.normal(loc=severity_mean, scale=severity_std, size=n_sims)
    sev_arr = np.abs(raw)

    s_sales   = inputs.sales * (1 + e[0] * sev_arr)
    s_opm     = inputs.opm   +  e[1] * sev_arr
    s_ebitda  = s_sales * s_opm

    d_dso  = e[2] * sev_arr
    d_inv  = e[3] * sev_arr
    wc_gap = (d_dso + d_inv) * s_sales / 365

    base_rate = inputs.interest / inputs.total_debt if inputs.total_debt > 0 else 0
    int_bps   = e[4] / 10000
    s_rate    = base_rate + int_bps * sev_arr
    s_int     = (inputs.total_debt + wc_gap) * s_rate
    s_tl_int  = inputs.tl_interest + inputs.total_debt * int_bps * sev_arr

    s_pbt  = s_ebitda - inputs.depreciation - s_int
    s_ca   = np.maximum(0, s_pbt) * 0.75 + inputs.depreciation

    denom  = inputs.tl_instalment + s_tl_int
    s_dscr = np.where(denom > 0, (s_ca + s_tl_int) / denom, 0)

    result = MonteCarloResult(
        scenario=scenario_name,
        n_sims=n_sims,
        dscr_mean=round(float(np.mean(s_dscr)), 4),
        dscr_p5=round(float(np.percentile(s_dscr,  5)), 4),
        dscr_p25=round(float(np.percentile(s_dscr, 25)), 4),
        dscr_p50=round(float(np.percentile(s_dscr, 50)), 4),
        dscr_p75=round(float(np.percentile(s_dscr, 75)), 4),
        dscr_p95=round(float(np.percentile(s_dscr, 95)), 4),
        prob_below_1=round(float(np.mean(s_dscr < 1.0)), 4),
        prob_below_covenant=round(float(np.mean(s_dscr < min_dscr)), 4),
        breakeven_severity=find_breakeven(inputs, scenario_idx, min_dscr),
        ladder=run_severity_ladder(inputs, scenario_idx, min_dscr),
    )

    if result.dscr_p5 < 1.0:
        result.verdict = "HIGH RISK — Tail scenarios cause debt service failure"
    elif result.dscr_p5 < min_dscr:
        result.verdict = "MODERATE — Tail scenarios breach covenant"
    elif result.prob_below_1 > 0.10:
        result.verdict = "WATCH — more than 10pct probability of DSCR below 1.0"
    else:
        result.verdict = "RESILIENT — Portfolio of outcomes is acceptable"

    return result


def run_cascade_from_dict(financials, scenario_name="Recession", min_dscr=1.25):
    """Entry point from excel_io.py."""
    try:
        inputs = CascadeInputs(
            sales         = financials.get("sales", 0),
            opm           = financials.get("opm", 0),
            ebitda        = financials.get("ebitda", 0),
            dso           = financials.get("dso", 0),
            inv_days      = financials.get("inv_days", 0),
            depreciation  = financials.get("depreciation", 0),
            interest      = financials.get("interest", 0),
            tl_interest   = financials.get("tl_interest", 0),
            tl_instalment = financials.get("tl_instalment", 0),
            stbb          = financials.get("stbb", 0),
            total_debt    = financials.get("total_debt", 0),
            cash_accruals = financials.get("cash_accruals", 0),
        )

        if inputs.sales <= 0:
            return {"error": "Sales must be greater than zero"}

        mc = run_monte_carlo(inputs, scenario_name, min_dscr)

        return {
            "scenario":           mc.scenario,
            "dscr_base":          _cascade_at_severity(
                                      inputs,
                                      SCENARIOS.index(scenario_name),
                                      0.0
                                  ).stressed_dscr,
            "dscr_mean":          mc.dscr_mean,
            "dscr_p5":            mc.dscr_p5,
            "dscr_p25":           mc.dscr_p25,
            "dscr_p50":           mc.dscr_p50,
            "dscr_p75":           mc.dscr_p75,
            "dscr_p95":           mc.dscr_p95,
            "prob_failure":       mc.prob_below_1,
            "prob_breach":        mc.prob_below_covenant,
            "breakeven_severity": mc.breakeven_severity,
            "verdict":            mc.verdict,
            "ladder":             [
                {
                    "severity": pt.severity,
                    "dscr":     pt.stressed_dscr,
                    "verdict":  pt.verdict,
                    "ebitda":   pt.stressed_ebitda,
                    "wc_gap":   pt.wc_gap,
                    "interest": pt.stressed_interest,
                }
                for pt in mc.ladder
            ],
        }

    except ValueError as e:
        return {"error": f"Unknown scenario: {e}"}
    except Exception as e:
        return {"error": str(e)}