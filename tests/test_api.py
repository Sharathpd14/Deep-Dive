import pytest
from fastapi.testclient import TestClient

import runner
from app import app
from config import settings
from db import connection, store

Q = "What are the main risks of investing in EV batteries?"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()
    monkeypatch.setattr(runner, "execute", lambda run_id: None)    # never run the real pipeline


@pytest.fixture
def client():
    return TestClient(app)     # no "with": lifespan is skipped, the temp DB is already set up


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_post_run_returns_202_and_a_queued_run(client):
    r = client.post("/runs", json={"question": Q})
    assert r.status_code == 202
    run = store.get_run(r.json()["run_id"])
    assert run["status"] == "queued" and run["question"] == Q


def test_question_is_validated(client):
    assert client.post("/runs", json={"question": "short"}).status_code == 422
    assert client.post("/runs", json={"question": "x" * 501}).status_code == 422


def test_concurrency_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_runs", 1)
    assert client.post("/runs", json={"question": Q}).status_code == 202
    assert client.post("/runs", json={"question": Q}).status_code == 429


def test_hourly_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_runs", 99)
    monkeypatch.setattr(settings, "max_runs_per_hour", 1)
    assert client.post("/runs", json={"question": Q}).status_code == 202
    assert client.post("/runs", json={"question": Q}).status_code == 429


def test_status_returns_only_new_events(client):
    rid = store.create_run(Q)
    store.log_event(rid, "planner", "plan_created", reason="4 sub-questions")
    store.log_event(rid, "researcher", "search")
    first = client.get(f"/runs/{rid}").json()
    assert [e["agent"] for e in first["events"]] == ["planner", "researcher"]
    later = client.get(f"/runs/{rid}?after={first['events'][0]['id']}").json()
    assert [e["agent"] for e in later["events"]] == ["researcher"]
    assert later["last_event_id"] == first["last_event_id"]


def test_unknown_run_is_404(client):
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/report").status_code == 404
    assert client.get("/runs/nope/view").status_code == 404


def test_report_not_ready_then_ready(client):
    rid = store.create_run(Q)
    assert client.get(f"/runs/{rid}/report").status_code == 409
    store.update_run(rid, status="done", report_md="# Title\n\nBody [[1]](https://a.com)")
    raw = client.get(f"/runs/{rid}/report")
    assert raw.status_code == 200 and raw.text.startswith("# Title")
    page = client.get(f"/runs/{rid}/report/view").text
    assert "<h1>Title</h1>" in page and 'href="https://a.com"' in page


def test_report_page_strips_scripts_and_event_handlers(client):
    rid = store.create_run(Q)
    store.update_run(rid, report_md="# T\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>")
    page = client.get(f"/runs/{rid}/report/view").text
    assert "alert(1)" not in page and "onerror" not in page


def test_claims_endpoint_lists_verdicts_and_sources(client):
    rid = store.create_run(Q)
    store.add_sub_question(rid, "How big is demand?", ["q"])
    store.add_evidence(rid, "Q1", "https://www.iea.org/x", "IEA", "Demand reached 950 GWh in 2024. " * 5, 1.0)
    c = store.save_claim(rid, "Q1", "Demand reached 950 GWh in 2024.", ["E1"])
    store.set_claim_verdict(rid, c.id, "unsupported", "", "quote not found in cited source")
    data = client.get(f"/runs/{rid}/claims").json()
    assert data[0]["verdict"] == "unsupported" and data[0]["reason"].startswith("quote not found")
    assert data[0]["sources"] == [{"url": "https://www.iea.org/x", "domain": "iea.org"}]


def test_index_escapes_user_text(client):
    store.create_run("<script>alert(1)</script> and some more words")
    assert "<script>alert(1)" not in client.get("/").text


def test_interrupted_runs_are_marked_failed_on_startup():
    rid = store.create_run(Q)
    store.update_run(rid, status="running")
    assert store.fail_stale_runs() == 1
    assert store.get_run(rid)["status"] == "failed"