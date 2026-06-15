"""API endpoint tests for the pure-compute routes.

TestClient is used without a context manager on purpose: the lifespan (and
with it the APScheduler daily-refresh job) must not start during tests.
DB-backed endpoints are covered via their underlying modules in the other
test files.
"""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

OHLSON_BODY = {
    "total_assets": 1500, "total_liabilities": 450,
    "current_assets": 700, "current_liabilities": 350,
    "working_capital": 350, "net_income": 180, "ffo": 220,
    "net_income_prev": 150,
}

CASCADE_BODY = {
    "sales": 2000, "opm": 0.10, "ebitda": 200, "dso": 33, "inv_days": 28,
    "depreciation": 26, "interest": 20, "tl_interest": 15,
    "tl_instalment": 20, "stbb": 80, "total_debt": 200,
    "cash_accruals": 146, "scenario": "Recession", "min_dscr": 1.25,
}


def test_health():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ohlson_valid():
    r = client.post("/ohlson", json=OHLSON_BODY)
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["prob_default"] <= 1.0
    assert "risk_band" in body


def test_ohlson_zero_assets_is_400():
    r = client.post("/ohlson", json={**OHLSON_BODY, "total_assets": 0})
    assert r.status_code == 400


def test_ohlson_missing_field_is_422():
    body = {k: v for k, v in OHLSON_BODY.items() if k != "total_assets"}
    r = client.post("/ohlson", json=body)
    assert r.status_code == 422


def test_cascade_valid():
    r = client.post("/cascade", json=CASCADE_BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["scenario"] == "Recession"
    assert body["dscr_p5"] <= body["dscr_p95"]
    assert len(body["ladder"]) == 6


def test_cascade_unknown_scenario_is_400():
    r = client.post("/cascade", json={**CASCADE_BODY, "scenario": "Nope"})
    assert r.status_code == 400


def test_narrative_job_not_found_is_404():
    r = client.get("/narrative/no-such-job")
    assert r.status_code == 404
