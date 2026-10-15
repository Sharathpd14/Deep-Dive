import pytest

from db import connection, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "test.db")
    connection.init_db()


def test_run_lifecycle():
    run_id = store.create_run("What are EV battery risks?")
    assert store.get_run(run_id)["status"] == "queued"
    store.update_run(run_id, status="running")
    assert store.get_run(run_id)["status"] == "running"


def test_update_run_rejects_unknown_field():
    run_id = store.create_run("q")
    with pytest.raises(ValueError):
        store.update_run(run_id, question="hacked")


def test_sub_questions_get_sequential_ids():
    run_id = store.create_run("q")
    a = store.add_sub_question(run_id, "Market size?", ["ev battery market size"])
    b = store.add_sub_question(run_id, "Key players?", ["ev battery makers"])
    assert (a.id, b.id) == ("Q1", "Q2")
    assert store.list_sub_questions(run_id)[0].queries == ["ev battery market size"]


def test_evidence_roundtrip_and_dedupe():
    run_id = store.create_run("q")
    e1 = store.add_evidence(run_id, "Q1", "https://www.iea.org/report", "IEA", "Sales hit 14M in 2023.", 1.0)
    dup = store.add_evidence(run_id, "Q1", "https://other.com/x", "Copy", "sales hit 14m  in 2023.", 0.4)
    assert e1 == "E1"
    assert dup is None                                   # near-identical text is rejected
    chunk = store.get_chunks(run_id, ["E1"])[0]
    assert chunk.domain == "iea.org" and chunk.tier == 1.0


def test_ids_are_scoped_per_run():
    r1, r2 = store.create_run("a"), store.create_run("b")
    assert store.add_evidence(r1, None, "https://a.com", "", "text one", 0.5) == "E1"
    assert store.add_evidence(r2, None, "https://b.com", "", "text two", 0.5) == "E1"


def test_claim_verdict_flow():
    run_id = store.create_run("q")
    c = store.save_claim(run_id, "Q1", "Sales hit 14M.", ["E1"])
    assert c.verdict == "pending"
    store.set_claim_verdict(run_id, c.id, "unsupported", "", "quote not found in source")
    rejected = store.list_claims(run_id, verdict="unsupported")
    assert len(rejected) == 1 and rejected[0].reason == "quote not found in source"


def test_trail_polling():
    run_id = store.create_run("q")
    store.log_event(run_id, "planner", "plan_created", reason="4 sub-questions")
    store.log_event(run_id, "researcher", "search", detail={"query": "ev risks"})
    events = store.list_events(run_id)
    assert [e.agent for e in events] == ["planner", "researcher"]
    assert len(store.list_events(run_id, after_id=events[0].id)) == 1