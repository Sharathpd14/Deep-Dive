import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean

from coverage import thin_subquestions
from db import store

RESULTS = Path(__file__).with_name("results.json")
PRIMARY_TIER = 0.8


def done_run_ids() -> list[str]:
    if not RESULTS.exists():
        return []
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    return [r["run_id"] for r in data["questions"].values() if r.get("status") == "done"]


def _initial_thin(events) -> int:
    """Sub-questions that were thin after the FIRST pass (the first decision the coordinator made)."""
    for e in events:
        if e.action == "loop_back":
            return len(e.detail.get("targets", []))
        if e.action == "loop_stopped":
            return len(e.detail.get("thin", []))
    return 0


def _seconds(run) -> float | None:
    try:
        return (datetime.fromisoformat(run["finished_at"])
                - datetime.fromisoformat(run["created_at"])).total_seconds()
    except (TypeError, ValueError):
        return None


def collect(run_id: str) -> dict:
    run = store.get_run(run_id)
    claims = store.list_claims(run_id)
    chunks = {c.id: c for c in store.list_evidence(run_id)}
    events = store.list_events(run_id)
    counts = Counter(c.verdict for c in claims)
    supported = [c for c in claims if c.verdict == "supported"]
    acts = Counter(e.action for e in events)
    sections = [e for e in events if e.action == "section_written"]

    tier_by_url: dict[str, float] = {}
    for c in supported:
        for eid in c.evidence_ids:
            if eid in chunks:
                tier_by_url[chunks[eid].url] = chunks[eid].tier

    return {
        "drafted": len(claims),
        "supported": counts["supported"], "partial": counts["partial"],
        "unsupported": counts["unsupported"], "contradicted": counts["contradicted"],
        "pending": counts["pending"],
        "pass_rate": counts["supported"] / len(claims) if claims else 0.0,
        "code_overrides": sum(1 for e in events
                              if e.action == "claim_rejected" and e.detail.get("code_override")),
        "sub_questions": len(store.list_sub_questions(run_id)),
        "thin_initial": _initial_thin(events),
        "thin_final": len(thin_subquestions(run_id)),
        "loops": run["loop_count"],
        "round0_supported": sum(1 for c in supported if c.round == 0),
        "sources_found": len({c.url for c in chunks.values()}),
        "sources_cited": len(tier_by_url),
        "mean_tier": mean(tier_by_url.values()) if tier_by_url else 0.0,
        "primary_share": (sum(t >= PRIMARY_TIER for t in tier_by_url.values()) / len(tier_by_url)
                          if tier_by_url else 0.0),
        "paragraphs_dropped": acts["paragraph_dropped"],
        "sentences_grounded": acts["sentences_grounded"],
        "fallback_sections": sum(1 for e in sections if e.detail.get("fallback")),
        "conflicts_flagged": sum(e.detail.get("conflicts", 0) for e in sections),
        "report_words": len((run["report_md"] or "").split()),
        "seconds": _seconds(run),
    }


def summarize(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("status") == "done"]
    if not rows:
        return {}
    drafted, supported = sum(r["drafted"] for r in rows), sum(r["supported"] for r in rows)
    n_sub = sum(r["sub_questions"] for r in rows) or 1
    secs = [r["seconds"] for r in rows if r.get("seconds")]
    return {
        "questions": len(rows), "drafted": drafted, "supported": supported,
        "removed": drafted - supported,
        "micro_pass_rate": supported / drafted if drafted else 0.0,
        "macro_pass_rate": mean(r["pass_rate"] for r in rows),
        "code_overrides": sum(r["code_overrides"] for r in rows),
        "completeness_initial": 1 - sum(r["thin_initial"] for r in rows) / n_sub,
        "completeness_final": 1 - sum(r["thin_final"] for r in rows) / n_sub,
        "round0_supported": sum(r["round0_supported"] for r in rows),
        "mean_loops": mean(r["loops"] for r in rows),
        "mean_sources_cited": mean(r["sources_cited"] for r in rows),
        "mean_tier": mean(r["mean_tier"] for r in rows),
        "primary_share": mean(r["primary_share"] for r in rows),
        "conflicts_flagged": sum(r["conflicts_flagged"] for r in rows),
        "mean_calls": mean(r.get("llm_calls", 0) for r in rows),
        "mean_tokens": mean(r.get("tokens", 0) for r in rows),
        "mean_seconds": mean(secs) if secs else 0.0,
    }