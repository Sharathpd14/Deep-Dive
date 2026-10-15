from pydantic import BaseModel, Field, ValidationError

import trail
from agents.prompts import RESEARCHER_REFINE_SYSTEM
from config import settings
from db import store
from llm import chat_json
from models import SubQuestion
from tools.retrieve import gather


class Refined(BaseModel):
    queries: list[str] = Field(min_length=1)


def refine_queries(run_id: str, sq: SubQuestion, reasons: list[str]) -> list[str]:
    """Ask a cheap model for new queries. Falls back to a simple variation if it fails."""
    domains = sorted({c.domain for c in store.list_evidence(run_id, sq.id)})
    user = (
        f"Sub-question: {sq.text}\n"
        f"Queries already tried: {sq.queries}\n"
        f"Domains already collected: {domains or 'none'}\n"
        f"Reasons earlier claims were rejected: {reasons or 'not enough claims survived'}"
    )
    messages = [
        {"role": "system", "content": RESEARCHER_REFINE_SYSTEM},
        {"role": "user", "content": user},
    ]
    tried = {q.lower() for q in sq.queries}
    try:
        new = Refined.model_validate(chat_json(messages, settings.cheap_model)).queries
    except (ValidationError, ValueError) as e:
        trail.log(run_id, "researcher", "refine_failed", reason=str(e)[:200], sub_q=sq.id)
        new = [f"{sq.text} official report data", f"{sq.text} statistics analysis"]

    out = []
    for q in new:
        q = " ".join(str(q).split())
        if q and q.lower() not in tried and q.lower() not in {o.lower() for o in out}:
            out.append(q)
    return out[:3]


def research(
    run_id: str,
    sub_questions: list[SubQuestion] | None = None,
    feedback: dict[str, list[str]] | None = None,
    round_: int = 0,
) -> dict:
    """Gather evidence for each sub-question.

    feedback maps sub_q_id -> rejection reasons. A sub-question with feedback is
    re-researched with new queries; one without is searched with its stored queries.
    """
    sub_questions = sub_questions or store.list_sub_questions(run_id)
    feedback = feedback or {}
    totals = {"sources": 0, "chunks": 0}

    for sq in sub_questions:
        if sq.id in feedback:
            queries = refine_queries(run_id, sq, feedback[sq.id])
            trail.log(run_id, "researcher", "queries_refined",
                      reason="; ".join(feedback[sq.id])[:300] or "thin evidence",
                      sub_q=sq.id, old=sq.queries, new=queries, round=round_)
            store.set_sub_question_queries(run_id, sq.id, sq.queries + queries)
        else:
            queries = sq.queries

        if not queries:
            trail.log(run_id, "researcher", "no_new_queries",
                      reason="refinement produced nothing new", sub_q=sq.id, round=round_)
            continue

        result = gather(run_id, sq.id, sq.text, queries)
        totals["sources"] += result["sources"]
        totals["chunks"] += result["chunks"]

        if result["chunks"] == 0:
            trail.log(run_id, "researcher", "no_new_evidence",
                      reason="no new usable sources found", sub_q=sq.id, round=round_)
        store.set_sub_question_status(run_id, sq.id, "researched")
        trail.log(run_id, "researcher", "sub_question_done",
                  sub_q=sq.id, round=round_, **result)

    trail.log(run_id, "researcher", "research_round_done",
              reason=f"round {round_}", **totals)
    return totals


if __name__ == "__main__":
    # uv run python -m agents.researcher "your question"
    import sys
    from agents.planner import plan
    from db.connection import init_db

    init_db()
    q = " ".join(sys.argv[1:]) or "What are the main risks of investing in the EV battery market in 2026?"
    rid = store.create_run(q)
    subqs = plan(rid, q)
    print(research(rid, subqs))
    for sq in store.list_sub_questions(rid):
        ev = store.list_evidence(rid, sq.id)
        print(f"\n{sq.id}: {sq.text}\n   {len(ev)} chunks from {len({e.domain for e in ev})} domains")
        for d in sorted({e.domain for e in ev}):
            print(f"     - {d}")
            
            
            
