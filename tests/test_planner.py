import pytest

from agents import planner
from db import connection, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


GOOD = {"sub_questions": [
    {"question": f"Sub-question number {i} about the topic?", "queries": [f"query {i}a", f"query {i}b", f"query {i}a"]}
    for i in range(1, 5)
]}


def test_plan_stores_subquestions_and_logs(monkeypatch):
    monkeypatch.setattr(planner, "chat_json", lambda messages, model, **kw: GOOD)
    run_id = store.create_run("q")
    subqs = planner.plan(run_id, "q")

    assert [s.id for s in subqs] == ["Q1", "Q2", "Q3", "Q4"]
    assert subqs[0].queries == ["query 1a", "query 1b"]          # duplicate removed
    assert len(store.list_sub_questions(run_id)) == 4
    assert "plan_created" in [e.action for e in store.list_events(run_id)]


def test_plan_retries_once_on_invalid_output(monkeypatch):
    replies = iter([{"wrong": "shape"}, GOOD])
    monkeypatch.setattr(planner, "chat_json", lambda messages, model, **kw: next(replies))
    run_id = store.create_run("q")
    assert len(planner.plan(run_id, "q")) == 4
    assert "plan_invalid" in [e.action for e in store.list_events(run_id)]


def test_plan_fails_after_two_invalid_outputs(monkeypatch):
    monkeypatch.setattr(planner, "chat_json", lambda messages, model, **kw: {"wrong": "shape"})
    run_id = store.create_run("q")
    with pytest.raises(ValueError):
        planner.plan(run_id, "q")


def test_plan_caps_subquestions_at_six(monkeypatch):
    many = {"sub_questions": [
        {"question": f"Sub-question number {i} about it?", "queries": [f"q{i}"]} for i in range(10)
    ]}
    monkeypatch.setattr(planner, "chat_json", lambda messages, model, **kw: many)
    run_id = store.create_run("q")
    assert len(planner.plan(run_id, "q")) == 6