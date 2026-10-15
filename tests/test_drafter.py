import pytest

from agents import drafter
from db import connection, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


@pytest.fixture
def run_with_evidence():
    run_id = store.create_run("q")
    sq = store.add_sub_question(run_id, "How big is the battery market?", ["q"])
    store.add_evidence(run_id, sq.id, "https://blog.example.com/a", "Blog", "Blog says the market is big. " * 5, 0.5)
    store.add_evidence(run_id, sq.id, "https://www.iea.org/r", "IEA", "IEA says demand reached 950 GWh in 2024. " * 5, 1.0)
    return run_id, sq


def _fake(output, captured=None):
    def fn(messages, model, **kw):
        if captured is not None:
            captured.append(messages)
        return output
    return fn


def test_saves_claims_with_valid_citations(monkeypatch, run_with_evidence):
    run_id, _sq = run_with_evidence
    monkeypatch.setattr(drafter, "chat_json", _fake({"claims": [
        {"claim": "Demand reached 950 GWh in 2024.", "evidence_ids": ["E2"]},
    ]}))
    assert drafter.draft(run_id) == {"Q1": 1}
    claim = store.list_claims(run_id)[0]
    assert claim.evidence_ids == ["E2"] and claim.verdict == "pending"


def test_drops_claim_citing_unknown_evidence(monkeypatch, run_with_evidence):
    run_id, _ = run_with_evidence
    monkeypatch.setattr(drafter, "chat_json", _fake({"claims": [
        {"claim": "The market will triple by 2030 for sure.", "evidence_ids": ["E99"]},
    ]}))
    assert drafter.draft(run_id) == {"Q1": 0}
    assert "claim_dropped" in [e.action for e in store.list_events(run_id)]


def test_removes_only_the_bad_ids(monkeypatch, run_with_evidence):
    run_id, _ = run_with_evidence
    monkeypatch.setattr(drafter, "chat_json", _fake({"claims": [
        {"claim": "Demand reached 950 GWh in 2024.", "evidence_ids": ["E2", "E99"]},
    ]}))
    drafter.draft(run_id)
    assert store.list_claims(run_id)[0].evidence_ids == ["E2"]
    assert "citation_fixed" in [e.action for e in store.list_events(run_id)]


def test_no_evidence_means_no_llm_call(monkeypatch):
    run_id = store.create_run("q")
    store.add_sub_question(run_id, "Anything at all here?", ["q"])
    monkeypatch.setattr(drafter, "chat_json", lambda *a, **k: pytest.fail("LLM should not be called"))
    assert drafter.draft(run_id) == {"Q1": 0}
    assert "no_evidence" in [e.action for e in store.list_events(run_id)]


def test_select_chunks_prefers_high_tier_and_caps(run_with_evidence):
    run_id, sq = run_with_evidence
    chunks = store.list_evidence(run_id, sq.id)
    picked = drafter.select_chunks(chunks, 1)
    assert [c.id for c in picked] == ["E2"]                     # iea.org, tier 1.0


def test_rejected_claims_are_shown_and_not_repeated(monkeypatch, run_with_evidence):
    run_id, sq = run_with_evidence
    old = store.save_claim(run_id, sq.id, "The market is enormous and growing fast.", ["E1"])
    store.set_claim_verdict(run_id, old.id, "unsupported", "", "vague")

    captured = []
    monkeypatch.setattr(drafter, "chat_json", _fake({"claims": [
        {"claim": "The market is enormous and growing fast.", "evidence_ids": ["E1"]},
        {"claim": "Demand reached 950 GWh in 2024.", "evidence_ids": ["E2"]},
    ]}, captured))
    drafter.draft(run_id, round_=1)

    assert "Previously rejected:\n- The market is enormous" in captured[0][1]["content"]
    texts = [c.text for c in store.list_claims(run_id)]
    assert texts.count("The market is enormous and growing fast.") == 1   # not re-added
    assert "Demand reached 950 GWh in 2024." in texts
    
    
def test_near_duplicate_claims_are_saved_once(monkeypatch, run_with_evidence):
    run_id, _ = run_with_evidence
    monkeypatch.setattr(drafter, "chat_json", _fake({"claims": [
        {"claim": "Demand reached 950 GWh in 2024 according to the IEA.", "evidence_ids": ["E2"]},
        {"claim": "According to the IEA, demand reached 950 GWh in 2024.", "evidence_ids": ["E2"]},
    ]}))
    assert drafter.draft(run_id) == {"Q1": 1}
    
def test_uncited_chunks_are_shown_first(run_with_evidence):
    run_id, sq = run_with_evidence
    chunks = store.list_evidence(run_id, sq.id)
    picked = drafter.select_chunks(chunks, 1, avoid={"E2"})      # E2 is the high-tier chunk, already cited
    assert [c.id for c in picked] == ["E1"]