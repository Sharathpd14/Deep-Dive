import sys
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import trail
from agents.drafter import draft
from agents.planner import plan
from agents.researcher import research
from agents.synthesizer import synthesize
from agents.verifier import out_of_credit, verify
from config import settings
from coverage import thin_subquestions, verified_stats
from db import store
from models import utcnow


class State(TypedDict, total=False):
    run_id: str
    question: str
    resume: bool                          # continue an existing run instead of planning
    round: int                            # 0 = first pass, then 1, 2, ... per loop-back
    targets: list[str]                    # sub-question IDs to re-research this round
    feedback: dict[str, list[str]]        # sub-question ID -> why it was thin
    progress: dict[str, dict]             # coverage at the last loop decision


# ---------- helpers ----------

def _target_subqs(state: State):
    ids = state.get("targets") or []
    if not ids:
        return None                       # None = every sub-question
    return [s for s in store.list_sub_questions(state["run_id"]) if s.id in ids]


def build_feedback(run_id: str, targets: list[str]) -> dict[str, list[str]]:
    """Tell the researcher WHY a sub-question is thin, so it searches differently."""
    stats = verified_stats(run_id)
    out: dict[str, list[str]] = {}
    for sq_id in targets:
        claims = store.list_claims(run_id, sq_id)
        reasons = [f"{c.verdict}: {c.reason[:160]}" for c in claims
                   if c.verdict in ("partial", "unsupported", "contradicted") and c.reason][:4]
        s = stats.get(sq_id, {"claims": 0, "domains": 0})
        if s["claims"] < settings.min_verified_claims:
            reasons.append(f"only {s['claims']} verified claim(s); need {settings.min_verified_claims}")
        if s["domains"] < settings.min_distinct_domains:
            reasons.append(f"verified claims come from {s['domains']} source domain(s); "
                           f"need {settings.min_distinct_domains} different domains")
        if not claims:
            reasons.append("no claims could be drafted from the evidence found")
        out[sq_id] = reasons
    return out


# ---------- nodes ----------

def plan_node(state: State):
    plan(state["run_id"], state["question"])
    return {"round": 0}


def research_node(state: State):
    rnd = state.get("round", 0)
    research(state["run_id"], sub_questions=_target_subqs(state),
             feedback=state.get("feedback") or None, round_=rnd)
    return {"round": rnd}


def draft_node(state: State):
    rnd = state.get("round", 0)
    draft(state["run_id"], sub_questions=_target_subqs(state), round_=rnd)
    return {"round": rnd}


def verify_node(state: State):
    rnd = state.get("round", 0)
    verify(state["run_id"], round_=rnd)
    return {"round": rnd}


def assess_node(state: State):
    """The coordinator's decision: loop back to research, or write the report."""
    run_id, rnd = state["run_id"], state.get("round", 0)
    stats, thin = verified_stats(run_id), thin_subquestions(run_id)
    previous = state.get("progress", {})

    targets = []
    for sq_id, s in thin.items():
        before = previous.get(sq_id)
        if before and s["claims"] <= before["claims"] and s["domains"] <= before["domains"]:
            trail.log(run_id, "coordinator", "loop_skipped", sub_q=sq_id,
                      reason="the last round added no verified claims or source domains")
            continue
        targets.append(sq_id)

    stop = None
    if rnd >= settings.max_loops:
        stop = f"reached the limit of {settings.max_loops} research loop(s)"
    elif out_of_credit():
        stop = "Mesh balance exhausted"

    if targets and not stop:
        feedback = build_feedback(run_id, targets)
        store.update_run(run_id, loop_count=rnd + 1)
        trail.log(run_id, "coordinator", "loop_back",
                  reason=f"{len(targets)} sub-question(s) lack verified evidence from enough sources",
                  round=rnd + 1, targets=targets, feedback=feedback)
        return {"targets": targets, "feedback": feedback, "round": rnd + 1, "progress": stats}

    if thin:
        trail.log(run_id, "coordinator", "loop_stopped",
                  reason=stop or "no progress on the remaining thin sub-questions",
                  thin=list(thin))
    return {"targets": [], "progress": stats}


def synthesize_node(state: State):
    synthesize(state["run_id"])
    return {"round": state.get("round", 0)}


# ---------- wiring ----------

def route_start(state: State) -> str:
    return "verify" if state.get("resume") else "plan"


def route_after_assess(state: State) -> str:
    return "research" if state.get("targets") else "synthesize"


def build_graph():
    g = StateGraph(State)
    g.add_node("plan", plan_node)
    g.add_node("research", research_node)
    g.add_node("draft", draft_node)
    g.add_node("verify", verify_node)
    g.add_node("assess", assess_node)
    g.add_node("synthesize", synthesize_node)

    g.add_conditional_edges(START, route_start, {"plan": "plan", "verify": "verify"})
    g.add_edge("plan", "research")
    g.add_edge("research", "draft")
    g.add_edge("draft", "verify")
    g.add_edge("verify", "assess")
    g.add_conditional_edges("assess", route_after_assess,
                            {"research": "research", "synthesize": "synthesize"})
    g.add_edge("synthesize", END)
    return g.compile()


# ---------- entry point used by the CLI and (in Phase 5) the API ----------

def run_pipeline(question: str | None = None, run_id: str | None = None) -> str:
    """Run the whole graph. Pass run_id to resume an existing run: pending claims get
    verified, then thin sub-questions are looped on."""
    resume = run_id is not None
    if resume:
        run = store.get_run(run_id)
        if not run:
            raise ValueError(f"No run with id {run_id}")
        question, start_round = run["question"], run["loop_count"]
    else:
        if not question:
            raise ValueError("A question is required")
        run_id, start_round = store.create_run(question), 0

    store.update_run(run_id, status="running", error=None)
    trail.log(run_id, "coordinator", "run_started",
              reason="resuming an existing run" if resume else "new run",
              question=question, max_loops=settings.max_loops)
    try:
        build_graph().invoke(
            {"run_id": run_id, "question": question, "resume": resume, "round": start_round},
            config={"recursion_limit": 40},
        )
    except Exception as e:
        store.update_run(run_id, status="failed", error=f"{type(e).__name__}: {e}"[:500],
                         finished_at=utcnow())
        trail.log(run_id, "coordinator", "run_failed", reason=f"{type(e).__name__}: {e}"[:300])
        raise

    store.update_run(run_id, status="done", finished_at=utcnow())
    trail.log(run_id, "coordinator", "run_finished", reason="report written")
    return run_id


def print_summary(run_id: str) -> None:
    run = store.get_run(run_id)
    claims = store.list_claims(run_id)
    counts = {v: sum(c.verdict == v for c in claims)
              for v in ("supported", "partial", "unsupported", "contradicted", "pending")}
    print(f"\nRun {run_id}: status={run['status']}, loops={run['loop_count']}")
    print(f"Claims: {counts}  ->  {counts['supported']}/{len(claims)} verified")

    stats, thin = verified_stats(run_id), thin_subquestions(run_id)
    for sq in store.list_sub_questions(run_id):
        s = stats.get(sq.id, {"claims": 0, "domains": 0})
        flag = "THIN" if sq.id in thin else "ok"
        print(f"  {sq.id} [{flag:4}] {s['claims']:2} verified claims, {s['domains']} domains  {sq.text[:60]}")

    for e in store.list_events(run_id):
        if e.action in ("loop_back", "loop_skipped", "loop_stopped"):
            print(f"  {e.action}: {e.reason} {e.detail.get('targets', '')}")

    path = Path("data") / f"report_{run_id}.md"
    path.write_text(run["report_md"] or "", encoding="utf-8")
    print(f"Report saved to {path}")


if __name__ == "__main__":
    #   uv run python -m graph "your question"       full new run
    #   uv run python -m graph --resume              latest run: verify pending, loop on thin sub-questions
    #   uv run python -m graph --resume <run_id>
    from db.connection import init_db

    init_db()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--resume" in sys.argv:
        rid = run_pipeline(run_id=args[0] if args else store.latest_run_id())
    else:
        rid = run_pipeline(" ".join(args) or
                           "What are the main risks of investing in the EV battery market in 2026?")
    print_summary(rid)