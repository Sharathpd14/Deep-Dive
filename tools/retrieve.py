import re
from concurrent.futures import ThreadPoolExecutor

import trail
from config import settings
from db import store
from tools.chunker import chunk_text
from tools.fetch import fetch_page
from tools.search import SearchResult, search
from tools.source_tiers import tier_for

_STOP = {"the", "and", "for", "are", "was", "with", "what", "how", "why", "that",
         "this", "from", "have", "has", "its", "into", "about", "main", "key"}


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _terms(s: str) -> set[str]:
    return {w for w in _words(s) if len(w) > 2 and w not in _STOP}


def score_chunk(text: str, terms: set[str]) -> float:
    """Share of the sub-question's key terms that appear in the chunk."""
    return len(terms & _words(text)) / len(terms) if terms else 0.0


def rank_candidates(results: list[SearchResult]) -> list[tuple[SearchResult, float]]:
    """Blend source trust (60%) with search rank (40%). Returns (result, tier)."""
    n = max(len(results), 1)
    scored = []
    for i, r in enumerate(results):
        tier = tier_for(r.url)
        scored.append((0.6 * tier + 0.4 * (1 - i / n), r, tier))
    scored.sort(key=lambda x: -x[0])
    return [(r, tier) for _, r, tier in scored]


def gather(
    run_id: str,
    sub_q_id: str,
    sub_q_text: str,
    queries: list[str],
    max_sources: int | None = None,
    exclude_urls: set[str] | None = None,
) -> dict:
    max_sources = max_sources or settings.max_sources_per_subq
    seen = store.evidence_urls(run_id) | (exclude_urls or set())

    candidates: list[SearchResult] = []
    seen_candidates: set[str] = set()
    for q in queries:
        try:
            results = search(q) 
        except Exception as e: # noqa: BLE001
            trail.log(run_id, "researcher", "search_failed", reason=str(e)[:200], sub_q=sub_q_id, query=q)
            continue
        trail.log(run_id, "researcher", "search", sub_q=sub_q_id, query=q, results=len(results))
        for r in results:
            if r.url not in seen and r.url not in seen_candidates:
                seen_candidates.add(r.url)
                candidates.append(r)

    ranked = rank_candidates(candidates)[: max_sources * 2]   # over-fetch: some will fail
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(lambda c: fetch_page(c[0].url), ranked))

    terms = _terms(sub_q_text + " " + " ".join(queries))
    sources_ok = chunks_stored = 0
    for (res, tier), page in zip(ranked, pages):
        if sources_ok >= max_sources:
            break
        if page is None:
            trail.log(run_id, "researcher", "fetch_failed",
                      reason="unreachable, non-HTML, or too little text",
                      sub_q=sub_q_id, url=res.url)
            continue

        chunks = chunk_text(page.text, settings.chunk_chars, settings.chunk_overlap)
        scored = sorted(((score_chunk(c, terms), c) for c in chunks), reverse=True)
        best = [c for s, c in scored if s > 0][: settings.max_chunks_per_source]
        if not best:
            trail.log(run_id, "researcher", "source_skipped",
                      reason="no chunk matched the sub-question terms",
                      sub_q=sub_q_id, url=res.url)
            continue

        ids = [store.add_evidence(run_id, sub_q_id, page.url,
                                  page.title or res.title, c, tier) for c in best]
        ids = [i for i in ids if i]               # None means duplicate text
        if ids:
            sources_ok += 1
            chunks_stored += len(ids)
            trail.log(run_id, "researcher", "source_stored",
                      reason=f"tier {tier}", sub_q=sub_q_id, url=page.url, chunk_ids=ids)

    return {"candidates": len(candidates), "sources": sources_ok, "chunks": chunks_stored}


if __name__ == "__main__":
    # Manual check: uv run python -m tools.retrieve "EV battery market risks 2026"
    import sys

    from db.connection import init_db

    init_db()
    question = " ".join(sys.argv[1:]) or "EV battery market risks 2026"
    rid = store.create_run(question)
    sq = store.add_sub_question(rid, question, [question])
    print(gather(rid, sq.id, question, [question]))
    for ch in store.list_evidence(rid):
        print(f"\n[{ch.id}] tier={ch.tier} {ch.domain}\n{ch.text[:200]}...")