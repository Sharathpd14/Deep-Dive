import pytest

from agents import researcher
from db import connection, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


@pytest.fixture
def run_with_subqs():
    run_id = store.create_run("q")
    store.add_sub_question(run_id, "How big is the battery market?", ["battery market size"])
    store.add_sub_question(run_id, "Who are the key players?", ["battery makers ranking"])
    return run_id


def test_round0_uses_planner_queries(monkeypatch, run_with_subqs):
    calls = []
    monkeypatch.setattr(
        researcher, "gather",
        lambda run_id, sq_id, text, queries, **kw: calls.append((sq_id, queries)) or {"candidates": 3, "sources": 2, "chunks": 5},
    )
    totals = researcher.research(run_with_subqs)

    assert calls == [("Q1", ["battery market size"]), ("Q2", ["battery makers ranking"])]
    assert totals == {"sources": 4, "chunks": 10}
    assert all(s.status == "researched" for s in store.list_sub_questions(run_with_subqs))


def test_feedback_triggers_refinement_only_for_that_subq(monkeypatch, run_with_subqs):
    calls = []
    monkeypatch.setattr(
        researcher, "gather",
        lambda run_id, sq_id, text, queries, **kw: calls.append((sq_id, queries)) or {"candidates": 1, "sources": 1, "chunks": 2},
    )
    monkeypatch.setattr(researcher, "chat_json",
                        lambda messages, model, **kw: {"queries": ["battery market IEA outlook", "battery market size"]})

    researcher.research(run_with_subqs, feedback={"Q1": ["source too vague"]}, round_=1)

    assert calls[0] == ("Q1", ["battery market IEA outlook"])      # repeated query was removed
    assert calls[1] == ("Q2", ["battery makers ranking"])          # untouched
    q1 = store.list_sub_questions(run_with_subqs)[0]
    assert q1.queries == ["battery market size", "battery market IEA outlook"]
    assert "queries_refined" in [e.action for e in store.list_events(run_with_subqs)]


def test_bad_refine_output_falls_back(monkeypatch, run_with_subqs):
    calls = []
    monkeypatch.setattr(
        researcher, "gather",
        lambda run_id, sq_id, text, queries, **kw: calls.append(queries) or {"candidates": 0, "sources": 0, "chunks": 0},
    )
    monkeypatch.setattr(researcher, "chat_json", lambda messages, model, **kw: {"nonsense": 1})

    researcher.research(run_with_subqs, feedback={"Q1": ["x"]}, round_=1)

    assert len(calls[0]) == 2 and "official report" in calls[0][0]
    actions = [e.action for e in store.list_events(run_with_subqs)]
    assert "refine_failed" in actions and "no_new_evidence" in actions