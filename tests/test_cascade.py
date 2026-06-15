"""Unit tests for the causal cascade + Monte Carlo module."""

from analytics.cascade import (
    CascadeInputs, SCENARIOS, _cascade_at_severity,
    run_cascade_from_dict, find_breakeven,
)

BASE = {
    "sales": 2000, "opm": 0.10, "ebitda": 200, "dso": 33, "inv_days": 28,
    "depreciation": 26, "interest": 20, "tl_interest": 15,
    "tl_instalment": 20, "stbb": 80, "total_debt": 200, "cash_accruals": 146,
}


def _inputs(**overrides):
    d = {**BASE, **overrides}
    return CascadeInputs(**d)


def test_zero_severity_equals_unstressed():
    pt = _cascade_at_severity(_inputs(), SCENARIOS.index("Recession"), 0.0)
    assert pt.stressed_sales == BASE["sales"]
    assert pt.stressed_opm == BASE["opm"]
    assert pt.wc_gap == 0.0


def test_dscr_declines_with_severity():
    idx = SCENARIOS.index("Recession")
    dscrs = [
        _cascade_at_severity(_inputs(), idx, sev).stressed_dscr
        for sev in (0.0, 0.5, 1.0, 1.5)
    ]
    assert dscrs == sorted(dscrs, reverse=True), "DSCR must fall as severity rises"


def test_monte_carlo_is_deterministic_with_seed():
    r1 = run_cascade_from_dict(BASE, scenario_name="Recession")
    r2 = run_cascade_from_dict(BASE, scenario_name="Recession")
    assert r1["dscr_p50"] == r2["dscr_p50"]
    assert r1["prob_failure"] == r2["prob_failure"]


def test_percentiles_are_ordered():
    r = run_cascade_from_dict(BASE, scenario_name="Demand Slump")
    assert r["dscr_p5"] <= r["dscr_p25"] <= r["dscr_p50"] <= r["dscr_p75"] <= r["dscr_p95"]


def test_every_scenario_runs():
    for scenario in SCENARIOS:
        r = run_cascade_from_dict(BASE, scenario_name=scenario)
        assert "error" not in r, f"{scenario}: {r.get('error')}"
        assert len(r["ladder"]) == 6


def test_zero_sales_rejected():
    r = run_cascade_from_dict({**BASE, "sales": 0}, scenario_name="Recession")
    assert "error" in r


def test_unknown_scenario_rejected():
    r = run_cascade_from_dict(BASE, scenario_name="Alien Invasion")
    assert "error" in r


def test_breakeven_none_for_resilient_borrower():
    # Strong borrower: severity 2.0 still keeps DSCR >= 1
    strong = _inputs(ebitda=600, opm=0.30, tl_instalment=5, interest=5,
                     tl_interest=4, total_debt=50)
    assert find_breakeven(strong, SCENARIOS.index("Recession")) is None


def test_breakeven_found_for_fragile_borrower():
    fragile = _inputs(ebitda=60, opm=0.03, tl_instalment=40)
    be = find_breakeven(fragile, SCENARIOS.index("Recession"))
    assert be is not None
    assert 0.0 <= be <= 2.0
