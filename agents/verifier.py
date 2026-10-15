import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

import trail
from agents.prompts import VERIFIER_SYSTEM
from config import settings
from db import store
from llm import chat_json
from models import Chunk, Claim, Verdict

MIN_QUOTE_CHARS = 20      # a quote shorter than this proves nothing
MIN_PART_CHARS = 10       # each piece of a quote that uses "..." must be substantial
WORKERS = 4

_CHAR_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ",
})
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ---------- the code checks (no LLM) ----------

def normalize(s: str) -> str:
    """Whitespace, case and typographic quotes/dashes only. Words are never changed."""
    return re.sub(r"\s+", " ", s.translate(_CHAR_MAP)).strip().lower()


def find_quote(quote: str, chunks: list[Chunk]) -> str | None:
    """Return the ID of the cited chunk that contains the quote, or None.
    A quote using '...' passes only if its parts appear in order in one chunk."""
    parts = [normalize(p).strip(" \"'") for p in re.split(r"\.\.\.|\u2026", quote)]
    parts = [p for p in parts if p]
    if not parts or max(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return None
    if any(len(p) < MIN_PART_CHARS for p in parts):
        return None
    for chunk in chunks:
        text, pos, ok = normalize(chunk.text), 0, True
        for part in parts:
            i = text.find(part, pos)
            if i < 0:
                ok = False
                break
            pos = i + len(part)
        if ok:
            return chunk.id
    return None


def numbers(s: str) -> set[str]:
    return {m.group().replace(",", "") for m in _NUMBER.finditer(s)}


def apply_checks(claim: Claim, chunks: list[Chunk], v: Verdict) -> tuple[Verdict, bool]:
    """Code has the final say. Returns (final verdict, whether code overrode the model)."""
    if v.verdict not in ("supported", "partial"):
        return v, False

    if find_quote(v.quote, chunks) is None:
        reason = f"quote not found in cited source (model said {v.verdict}: {v.reason})"
        return Verdict(verdict="unsupported", quote="", reason=reason[:500]), True

    if v.verdict == "supported":
        missing = numbers(claim.text) - numbers(v.quote)
        if missing:
            reason = f"claim has number(s) not in the quote: {sorted(missing)}"
            return Verdict(verdict="partial", quote=v.quote, reason=reason), True
    return v, False


# ---------- the LLM judgement ----------

def judge(claim: Claim, chunks: list[Chunk]) -> Verdict:
    sources = "\n\n".join(f"[{c.id}] (source: {c.domain})\n{c.text}" for c in chunks)
    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM},
        {"role": "user", "content": f"Claim: {claim.text}\n\nCited source text:\n{sources}"},
    ]
    last_error = None
    for _ in range(2):
        raw = chat_json(messages, settings.verifier_model, temperature=0.0)
        if isinstance(raw, dict) and isinstance(raw.get("verdict"), str):
            raw["verdict"] = raw["verdict"].strip().lower()
        try:
            return Verdict.model_validate(raw)
        except ValidationError as e:
            last_error = e
            messages += [
                {"role": "assistant", "content": str(raw)},
                {"role": "user", "content": "Invalid shape. Return only the corrected JSON."},
            ]
    raise ValueError(f"verifier returned invalid JSON twice: {str(last_error)[:150]}")


def _work(job: tuple[Claim, list[Chunk]]) -> tuple[Verdict | None, str | None]:
    claim, chunks = job
    if not chunks:
        return Verdict(verdict="unsupported", reason="cited evidence not found"), None
    try:
        return judge(claim, chunks), None
    except Exception as e:                    # one bad call must not kill the round
        return None, f"{type(e).__name__}: {e}"[:300]


# ---------- the round ----------

def verify(run_id: str, round_: int = 0) -> dict:
    """Verify every pending claim. Claims whose LLM call fails stay pending."""
    claims = store.list_claims(run_id, verdict="pending")
    jobs = [(c, store.get_chunks(run_id, c.evidence_ids)) for c in claims]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(_work, jobs))

    counts: Counter = Counter()
    for (claim, chunks), (verdict, error) in zip(jobs, results):
        if verdict is None:
            counts["error"] += 1
            trail.log(run_id, "verifier", "verify_error", reason=error or "unknown",
                      claim_id=claim.id, sub_q=claim.sub_q_id, round=round_)
            continue

        final, overridden = apply_checks(claim, chunks, verdict)
        store.set_claim_verdict(run_id, claim.id, final.verdict, final.quote, final.reason)
        counts[final.verdict] += 1
        trail.log(
            run_id, "verifier",
            "claim_verified" if final.verdict == "supported" else "claim_rejected",
            reason=final.reason, claim_id=claim.id, sub_q=claim.sub_q_id,
            llm_verdict=verdict.verdict, final_verdict=final.verdict,
            code_override=overridden, evidence_ids=claim.evidence_ids, round=round_,
        )

    summary = {k: counts.get(k, 0) for k in
               ("supported", "partial", "unsupported", "contradicted", "error")}
    trail.log(run_id, "verifier", "verify_round_done", reason=f"round {round_}", **summary)
    return summary


if __name__ == "__main__":
    # Verifies the pending claims of your latest run (or pass a run id):
    #   uv run python -m agents.verifier
    import sys
    from db.connection import init_db

    init_db()
    rid = sys.argv[1] if len(sys.argv) > 1 else store.latest_run_id()
    summary = verify(rid)
    total = sum(summary.values()) - summary["error"]
    print(f"Run {rid}: {summary}")
    if total:
        print(f"Pass rate: {summary['supported']}/{total} = {summary['supported'] / total:.0%}")

    for c in store.list_claims(rid):
        if c.verdict in ("unsupported", "contradicted", "partial"):
            print(f"\n[{c.verdict.upper()}] {c.id}: {c.text}\n    why: {c.reason}")
    print("\n--- sample of supported claims ---")
    for c in [c for c in store.list_claims(rid, verdict="supported")][:3]:
        print(f"\n{c.id}: {c.text}\n    quote: \"{c.quote}\"")