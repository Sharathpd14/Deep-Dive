import pytest

import seed
from db import connection, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "a.db")
    connection.init_db()


def _make_run():
    rid = store.create_run("What are the risks of batteries?")
    store.add_sub_question(rid, "How big is demand?", ["q"])
    store.add_evidence(rid, "Q1", "https://www.iea.org/x", "IEA", "Demand reached 950 GWh. " * 5, 1.0)
    c = store.save_claim(rid, "Q1", "Demand reached 950 GWh.", ["E1"])
    store.set_claim_verdict(rid, c.id, "supported", "Demand reached 950 GWh.", "stated")
    store.log_event(rid, "planner", "plan_created", reason="1 sub-question")
    store.log_event(rid, "researcher", "search")
    store.update_run(rid, status="done", report_md="# Report", loop_count=1)
    return rid


def test_export_then_import_into_a_fresh_database(tmp_path, monkeypatch):
    rid = _make_run()
    path = seed.export(rid, tmp_path / "demo.json")
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "b.db")
    connection.init_db()

    assert seed.ensure_demo(path) == rid
    run = store.get_run(rid)
    assert run["report_md"] == "# Report" and run["loop_count"] == 1 and run["status"] == "done"
    assert [c.verdict for c in store.list_claims(rid)] == ["supported"]
    assert store.get_chunks(rid, ["E1"])[0].tier == 1.0
    assert [e.action for e in store.list_events(rid)] == ["plan_created", "search"]


def test_import_is_idempotent(tmp_path):
    rid = _make_run()
    path = seed.export(rid, tmp_path / "demo.json")
    assert seed.ensure_demo(path) == rid
    assert seed.ensure_demo(path) == rid
    assert len(store.list_claims(rid)) == 1 and len(store.list_events(rid)) == 2


def test_no_demo_file_means_no_demo(tmp_path):
    assert seed.ensure_demo(tmp_path / "missing.json") is None
    assert seed.demo_run(tmp_path / "missing.json") is None