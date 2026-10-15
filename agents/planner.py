from datetime import date

from pydantic import BaseModel, Field, ValidationError

import trail
from agents.prompts import PLANNER_SYSTEM
from config import settings
from db import store
from llm import chat_json
from models import SubQuestion

MAX_SUBQS = 6
MAX_QUERIES = 3


class PlanItem(BaseModel):
    question: str = Field(min_length=10)
    queries: list[str] = Field(min_length=1)


class Plan(BaseModel):
    sub_questions: list[PlanItem] = Field(min_length=3)


def _clean_queries(queries: list[str]) -> list[str]:
    seen, out = set(), []
    for q in queries:
        q = " ".join(str(q).split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:MAX_QUERIES]


def plan(run_id: str, question: str) -> list[SubQuestion]:
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": f"Today's date: {date.today().isoformat()}\n\nResearch question: {question}"},
    ]

    parsed: Plan | None = None
    for attempt in range(2):
        raw = chat_json(messages, settings.planner_model)
        try:
            parsed = Plan.model_validate(raw)
            break
        except ValidationError as e:
            trail.log(run_id, "planner", "plan_invalid", reason=str(e)[:300], attempt=attempt + 1)
            messages += [
                {"role": "assistant", "content": str(raw)},
                {"role": "user", "content": "Your JSON did not match the required shape. "
                                            "Return only the corrected JSON."},
            ]
    if parsed is None:
        raise ValueError("Planner failed to produce a valid plan after 2 attempts")

    subqs = []
    for item in parsed.sub_questions[:MAX_SUBQS]:
        queries = _clean_queries(item.queries)
        if queries:
            subqs.append(store.add_sub_question(run_id, item.question.strip(), queries))

    trail.log(run_id, "planner", "plan_created",
              reason=f"{len(subqs)} sub-questions",
              sub_questions=[{"id": s.id, "text": s.text, "queries": s.queries} for s in subqs])
    return subqs


if __name__ == "__main__":
    # Manual check: uv run python -m agents.planner "What are the main risks of investing in EV batteries in 2026?"
    import sys

    from db.connection import init_db

    init_db()
    q = " ".join(sys.argv[1:]) or "What are the main risks of investing in the EV battery market in 2026?"
    rid = store.create_run(q)
    for s in plan(rid, q):
        print(f"\n{s.id}: {s.text}")
        for query in s.queries:
            print(f"   - {query}")