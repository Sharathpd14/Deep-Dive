from config import settings
from db import store


def verified_stats(run_id: str) -> dict[str, dict]:
    """Per sub-question: number of supported claims and distinct source domains behind them."""
    evidence = {c.id: c for c in store.list_evidence(run_id)}
    stats = {sq.id: {"claims": 0, "domains": set()} for sq in store.list_sub_questions(run_id)}
    for claim in store.list_claims(run_id, verdict="supported"):
        s = stats.setdefault(claim.sub_q_id, {"claims": 0, "domains": set()})
        s["claims"] += 1
        for eid in claim.evidence_ids:
            if eid in evidence:
                s["domains"].add(evidence[eid].domain)
    return {k: {"claims": v["claims"], "domains": len(v["domains"])} for k, v in stats.items()}


def thin_subquestions(run_id: str) -> dict[str, dict]:
    """Sub-questions without enough verified claims from enough distinct domains."""
    return {
        sq_id: s for sq_id, s in verified_stats(run_id).items()
        if s["claims"] < settings.min_verified_claims or s["domains"] < settings.min_distinct_domains
    }