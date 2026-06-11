"""Unit tests for the 4-agent credit committee with Ollama fully mocked."""
import sqlite3
import pytest

from agents import committee
from agents.committee import run_committee, MODELS


SUPERVISOR_JSON = (
    '{"verdict": "APPROVE WITH COVENANTS", "confidence": "MEDIUM", '
    '"key_risk": "Leverage trending up", "needs_review": true}'
)


class FakeOllama:
    """Returns canned responses; records every call for assertions."""
    def __init__(self, supervisor_text=SUPERVISOR_JSON, fail_models=()):
        self.calls = []
        self.supervisor_text = supervisor_text
        self.fail_models = fail_models

    def chat(self, model, messages, options):
        self.calls.append(model)
        if model in self.fail_models:
            raise ConnectionError("model unavailable")
        if model == MODELS["supervisor"]:
            content = self.supervisor_text
        elif model == MODELS["underwriter"]:
            content = "CREDIT JUSTIFICATION MEMORANDUM\n\nAll numbers preserved."
        else:
            content = "- Finding A (metric X)\n- Finding B (metric Y)"
        return {"message": {"content": content}}


def test_committee_full_run(seeded_db, monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(committee, "OLLAMA", fake)

    result = run_committee(borrower_name="Test Borrower Ltd")

    assert result.error == ""
    assert result.verdict == "APPROVE WITH COVENANTS"
    assert result.confidence == "MEDIUM"
    assert result.key_risk == "Leverage trending up"
    assert len(result.audit_log) == 4
    assert all(not e["error"] for e in result.audit_log)
    # Attribution footer must name the models used
    assert MODELS["underwriter"] in result.final_memo

    # Narrative audit trail persisted
    with sqlite3.connect(seeded_db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM narrative").fetchone()[0]
    assert n == 4


def test_committee_parses_json_inside_markdown_fence(seeded_db, monkeypatch):
    fenced = f"```json\n{SUPERVISOR_JSON}\n```"
    monkeypatch.setattr(committee, "OLLAMA", FakeOllama(supervisor_text=fenced))
    result = run_committee(borrower_name="Test Borrower Ltd")
    assert result.verdict == "APPROVE WITH COVENANTS"


def test_committee_defaults_to_refer_on_bad_json(seeded_db, monkeypatch):
    monkeypatch.setattr(committee, "OLLAMA",
                        FakeOllama(supervisor_text="I think it looks fine."))
    result = run_committee(borrower_name="Test Borrower Ltd")
    assert result.verdict == "REFER"
    assert result.confidence == "LOW"


def test_committee_survives_agent_failure(seeded_db, monkeypatch):
    monkeypatch.setattr(
        committee, "OLLAMA",
        FakeOllama(fail_models=(MODELS["forensic"],))
    )
    result = run_committee(borrower_name="Test Borrower Ltd")
    forensic_entry = next(e for e in result.audit_log if e["agent"] == "forensic")
    assert "model unavailable" in forensic_entry["error"]
    # The committee still completes and produces a memo
    assert result.final_memo != ""


def test_committee_no_borrower(temp_db, monkeypatch):
    monkeypatch.setattr(committee, "OLLAMA", FakeOllama())
    result = run_committee()
    assert result.error == "No borrower found in database"
