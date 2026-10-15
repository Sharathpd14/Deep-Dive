from pydantic import BaseModel, Field, ValidationError

import trail
from agents.prompts import DRAFTER_SYSTEM
from config import settings
from db import store
from llm import chat_json
from models import Chunk, SubQuestion


class DraftClaim(BaseModel):
    claim: str = Field(min_length=15)
    evidence_ids: list[str] = Field(min_length=1)


class DraftOut(BaseModel):
    claims: list[DraftClaim] = Field(default_factory=list)


def select_chunks(chunks: list[Chunk], limit: int) -> list[Chunk]:
    """Best sources first. Stable sort keeps retrieval order within a tier."""
    return sorted(chunks, key=lambda c: -c.tier)[:limit]


def _format_chunks(chunks: list[Chunk]) -> str:
    return "\n\n".join(
        f"[{c.id}] (source: {c.domain}, trust {c.tier})\n{c.text}" for c in chunks
    )


def _format_list(items: list[str]) -> str:
    return "\n".join(f"- {t}" for t in items) if items else "none"


def draft_subquestion(run_id: str, sq: SubQuestion, round_: int = 0) -> int:
    all_chunks = store.list_evidence(run_id, sq.id)
    if not all_chunks:
        trail.log(run_id, "drafter", "no_evidence",
                  reason="no stored evidence for this sub-question", sub_q=sq.id, round=round_)
        return 0

    chunks = select_chunks(all_chunks, settings.drafter_max_chunks)
    shown_ids = {c.id for c in chunks}

    prior = store.list_claims(run_id, sq.id)
    covered = [c.text for c in prior if c.verdict in ("supported", "partial")]
    rejected = [c.text for c in prior if c.verdict in ("unsupported", "contradicted")]

    user = (
        f"Sub-question: {sq.text}\n\n"
        f"Evidence:\n{_format_chunks(chunks)}\n\n"
        f"Already covered:\n{_format_list(covered)}\n\n"
        f"Previously rejected:\n{_format_list(rejected)}"
    )
    messages = [
        {"role": "system", "content": DRAFTER_SYSTEM},
        {"role": "user", "content": user},
    ]

    parsed: DraftOut | None = None
    for attempt in range(2):
        raw = chat_json(messages, settings.drafter_model, temperature=0.1)
        try:
            parsed = DraftOut.model_validate(raw)
            break
        except ValidationError as e:
            trail.log(run_id, "drafter", "draft_invalid", reason=str(e)[:300],
                      sub_q=sq.id, attempt=attempt + 1)
            messages += [
                {"role": "assistant", "content": str(raw)},
                {"role": "user", "content": "Your JSON did not match the required shape. "
                                            "Return only the corrected JSON."},
            ]
    if parsed is None:
        trail.log(run_id, "drafter", "draft_failed",
                  reason="invalid output twice", sub_q=sq.id, round=round_)
        return 0

    existing = {t.lower() for t in covered + rejected}
    saved = 0
    for item in parsed.claims[: settings.max_claims_per_subq]:
        text = " ".join(item.claim.split())
        if text.lower() in existing:
            continue

        valid = [i for i in dict.fromkeys(item.evidence_ids) if i in shown_ids]
        invalid = [i for i in item.evidence_ids if i not in shown_ids]
        if not valid:
            trail.log(run_id, "drafter", "claim_dropped",
                      reason="cites evidence IDs that were not provided",
                      sub_q=sq.id, claim=text, bad_ids=invalid)
            continue
        if invalid:
            trail.log(run_id, "drafter", "citation_fixed",
                      reason="removed unknown evidence IDs", sub_q=sq.id, bad_ids=invalid)

        claim = store.save_claim(run_id, sq.id, text, valid, round_=round_)
        existing.add(text.lower())
        saved += 1
        trail.log(run_id, "drafter", "claim_drafted",
                  sub_q=sq.id, claim_id=claim.id, evidence_ids=valid, round=round_)

    return saved


def draft(
    run_id: str, sub_questions: list[SubQuestion] | None = None, round_: int = 0
) -> dict:
    sub_questions = sub_questions or store.list_sub_questions(run_id)
    per_subq = {sq.id: draft_subquestion(run_id, sq, round_) for sq in sub_questions}
    trail.log(run_id, "drafter", "draft_round_done",
              reason=f"round {round_}", claims=sum(per_subq.values()), per_subq=per_subq)
    return per_subq


if __name__ == "__main__":
    # Reuses your latest run's stored evidence, so no new searching:
    #   uv run python -m agents.drafter            (latest run)
    #   uv run python -m agents.drafter <run_id>
    import sys
    from db.connection import init_db

    init_db()
    rid = sys.argv[1] if len(sys.argv) > 1 else store.latest_run_id()
    print(f"Run {rid}: {draft(rid)}")
    for c in store.list_claims(rid):
        print(f"\n{c.id} [{c.sub_q_id}] {c.text}\n    cites {c.evidence_ids}")