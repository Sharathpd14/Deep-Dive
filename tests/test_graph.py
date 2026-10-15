import pytest

import db.connection as connection
import graph
from config import settings
from db import store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


@pytest.fixture
def fake_agents(monkeypatch):
    """Q1 is well covered in round 0. Q2 is thin until round 1 finds a second domain."""
    fake = {"research": [], "improve": True}

    def add(run_id, sq_id, domain, n):
        store.add_evidence(run_id, sq_id, f"https://{domain}/{sq_id}/{n}", domain,
                           f"Unique evidence text {sq_id} {domain} {n} " * 5, 0.5)

    def plan(run_id, question):
        return [store.add_sub_question(run_id, "How big is demand?", ["demand"]),
                store.add_sub_question(run_id, "Who are the key players?", ["players"])]

    def research(run_id, sub_questions=None, feedback=None, round_=0):
        fake["research"].append({"ids": [s.id for s in sub_questions] if sub_questions else None,
                                 "feedback": feedback, "round": round_})
        if round_ == 0:
            add(run_id, "Q1", "a.com", 1)
            add(run_id, "Q1", "b.com", 1)
            add(run_id, "Q2", "a.com", 2)
        elif round_ == 1 and fake["improve"]:
            add(run_id, "Q2", "c.com", 3)
        return {"sources": 1, "chunks": 1}

    def draft(run_id, sub_questions=None, round_=0):
        for sq in sub_questions or store.list_sub_questions(run_id):
            cited = {e for c in store.list_claims(run_id, sq.id) for e in c.evidence_ids}
            for ch in store.list_evidence(run_id, sq.id):
                if ch.id not in cited:
                    store.save_claim(run_id, sq.id, f"Claim drawn from {ch.id}", [ch.id], round_)
        return {}

    def verify(run_id, round_=0):
        for c in store.list_claims(run_id, verdict="pending"):
            store.set_claim_verdict(run_id, c.id, "supported", "a verbatim quote", "ok")
        return {}

    def synthesize(run_id):
        store.update_run(run_id, report_md="REPORT")
        return "REPORT"

    monkeypatch.setattr(graph, "plan", plan)
    monkeypatch.setattr(graph, "research", research)
    monkeypatch.setattr(graph, "draft", draft)
    monkeypatch.setattr(graph, "verify", verify)
    monkeypatch.setattr(graph, "synthesize", synthesize)
    monkeypatch.setattr(graph, "out_of_credit", lambda: False)
    return fake


def _actions(run_id):
    return [e.action for e in store.list_events(run_id)]


def test_thin_subquestion_triggers_one_targeted_loop(fake_agents):
    run_id = graph.run_pipeline("What drives demand?")
    calls = fake_agents["research"]

    assert len(calls) == 2
    assert calls[0]["ids"] is None and calls[0]["round"] == 0           # round 0: every sub-question
    assert calls[1]["ids"] == ["Q2"] and calls[1]["round"] == 1         # round 1: only the thin one
    assert list(calls[1]["feedback"]) == ["Q2"]

    run = store.get_run(run_id)
    assert run["status"] == "done" and run["loop_count"] == 1 and run["report_md"] == "REPORT"
    assert "loop_back" in _actions(run_id) and "run_finished" in _actions(run_id)


def test_loop_stops_when_a_round_adds_nothing(fake_agents):
    fake_agents["improve"] = False
    run_id = graph.run_pipeline("What drives demand?")

    assert len(fake_agents["research"]) == 2                            # not 3: no progress, so no round 2
    assert "loop_skipped" in _actions(run_id)
    assert store.get_run(run_id)["loop_count"] == 1


def test_max_loops_zero_disables_the_loop(fake_agents, monkeypatch):
    monkeypatch.setattr(settings, "max_loops", 0)
    run_id = graph.run_pipeline("What drives demand?")

    assert len(fake_agents["research"]) == 1
    assert "loop_stopped" in _actions(run_id) and "loop_back" not in _actions(run_id)


def test_out_of_credit_stops_the_loop(fake_agents, monkeypatch):
    monkeypatch.setattr(graph, "out_of_credit", lambda: True)
    run_id = graph.run_pipeline("What drives demand?")

    assert len(fake_agents["research"]) == 1
    stopped = [e for e in store.list_events(run_id) if e.action == "loop_stopped"]
    assert "balance" in stopped[0].reason


def test_resume_skips_planning_and_loops_on_thin_subquestions(fake_agents):
    first = graph.run_pipeline("What drives demand?")
    # Pretend the first run was cut short: wipe Q2's second domain by starting a fresh partial run.
    run_id = store.create_run("Resumed question")
    store.add_sub_question(run_id, "Who are the key players?", ["players"])
    store.add_evidence(run_id, "Q1", "https://a.com/x", "", "Resumed evidence text " * 5, 0.5)
    c = store.save_claim(run_id, "Q1", "A single claim from one domain.", ["E1"])
    store.set_claim_verdict(run_id, c.id, "supported", "quote", "ok")
    fake_agents["research"].clear()

    graph.run_pipeline(run_id=run_id)

    assert fake_agents["research"][0]["ids"] == ["Q1"] and fake_agents["research"][0]["round"] == 1
    assert len(store.list_sub_questions(run_id)) == 1                   # planner did not run again


def test_feedback_names_the_rejection_reasons_and_the_domain_gap():
    run_id = store.create_run("q")
    store.add_sub_question(run_id, "Who are the key players?", ["q"])
    store.add_evidence(run_id, "Q1", "https://a.com/x", "", "text one " * 20, 0.5)
    c = store.save_claim(run_id, "Q1", "Some claim about the players here.", ["E1"])
    store.set_claim_verdict(run_id, c.id, "partial", "quote", "quote covers only 30% of the claim")

    joined = " ".join(graph.build_feedback(run_id, ["Q1"])["Q1"])
    assert "covers only 30%" in joined
    assert "domain" in joined