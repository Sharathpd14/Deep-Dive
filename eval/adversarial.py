"""Plant known-false claims next to real evidence and count how many get through.
  uv run python -m eval.adversarial --per-type 20
"""
import argparse
import json
import random
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from agents.verifier import allowed_numbers_for, apply_checks, judge
from config import settings
from db import store
from db.connection import init_db
from eval import metrics, report
from models import Claim
from textcheck import content_words

OUT = Path(__file__).with_name("adversarial.json")
KINDS = ["number_swap", "reversal", "overreach", "wrong_source"]
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_FLIPS = {"increase": "decrease", "increased": "decreased", "decrease": "increase",
          "decreased": "increased", "grow": "shrink", "grew": "shrank", "rise": "fall",
          "rose": "fell", "fell": "rose", "higher": "lower", "lower": "higher",
          "more": "less", "faster": "slower", "slower": "faster", "expensive": "cheap",
          "largest": "smallest", "leader": "laggard", "reduce": "increase", "reduced": "increased"}
_FLIP_RE = re.compile(r"\b(" + "|".join(_FLIPS) + r")\b", re.IGNORECASE)
_OVERREACH = [" because of sharply rising government subsidies worldwide",
              ", which analysts say will double within two years",
              ", a trend driven mainly by a recent wave of corporate bankruptcies"]


# ---------- the four corruptions (pure functions, tested offline) ----------

def swap_number(text: str, forbidden: set[str] = frozenset()) -> str | None:
    for m in _NUM.finditer(text):
        raw = m.group()
        plain = raw.replace(",", "")
        if plain in forbidden:
            continue
        try:
            val = float(plain)
        except ValueError:
            continue
        new = val + 3 if 1900 <= val <= 2100 else (val * 3 if val >= 10 else val + 7)
        if "." in raw:
            shown = f"{new:.1f}"
        elif "," in raw:
            shown = f"{int(new):,}"
        else:
            shown = str(int(new))
        return text[:m.start()] + shown + text[m.end():]
    return None


def flip_meaning(text: str) -> str | None:
    m = _FLIP_RE.search(text)
    if not m:
        return None
    new = _FLIPS[m.group(1).lower()]
    if m.group(1)[0].isupper():
        new = new.capitalize()
    return text[:m.start()] + new + text[m.end():]


def add_overreach(text: str, rng: random.Random) -> str:
    return text.rstrip(" .") + rng.choice(_OVERREACH) + "."


def pick_unrelated_chunk(claim, chunks, rng, max_overlap: float = 0.4):
    words = content_words(claim.text)
    if not words:
        return None
    ok = [c for c in chunks if c.id not in claim.evidence_ids
          and len(words & content_words(c.text)) / len(words) <= max_overlap]
    return rng.choice(ok) if ok else None


def build_cases(pool, chunks_by_run, per_type=20, seed=7, forbidden_for=lambda run_id, claim: set()):
    rng = random.Random(seed)
    pool = list(pool)
    rng.shuffle(pool)
    cases, made = [], Counter()

    def add(kind, run_id, claim, text, evidence_ids):
        if made[kind] >= per_type:
            return
        made[kind] += 1
        cases.append({"id": f"{kind}-{made[kind]}", "kind": kind, "run_id": run_id,
                      "origin": claim.id, "sub_q_id": claim.sub_q_id, "original": claim.text,
                      "text": text, "evidence_ids": evidence_ids,
                      "expected": "accept" if kind == "control" else "reject"})

    for run_id, claim in pool:
        add("control", run_id, claim, claim.text, claim.evidence_ids)
        if (t := swap_number(claim.text, forbidden_for(run_id, claim))):
            add("number_swap", run_id, claim, t, claim.evidence_ids)
        if (t := flip_meaning(claim.text)):
            add("reversal", run_id, claim, t, claim.evidence_ids)
        add("overreach", run_id, claim, add_overreach(claim.text, rng), claim.evidence_ids)
        if (other := pick_unrelated_chunk(claim, chunks_by_run[run_id], rng)):
            add("wrong_source", run_id, claim, claim.text, [other.id])
    return cases


# ---------- judging ----------

def evaluate(case: dict) -> dict:
    run_id = case["run_id"]
    claim = Claim(id=case["id"], run_id=run_id, sub_q_id=case["sub_q_id"],
                  text=case["text"], evidence_ids=case["evidence_ids"])
    chunks = store.get_chunks(run_id, claim.evidence_ids)
    try:
        v = judge(claim, chunks)
    except Exception as e: # noqa: BLE001
        return {**case, "error": f"{type(e).__name__}: {e}"[:200]}
    final, overridden = apply_checks(claim, chunks, v, allowed_numbers_for(run_id, claim))
    return {**case, "llm_verdict": v.verdict, "final_verdict": final.verdict, "code_override": overridden}


def stats(results: list[dict]) -> dict:
    out = {}
    for kind in KINDS + ["control"]:
        rows = [r for r in results if r["kind"] == kind and "final_verdict" in r]
        if not rows:
            continue
        accepted_llm = sum(r["llm_verdict"] == "supported" for r in rows)
        accepted_final = sum(r["final_verdict"] == "supported" for r in rows)
        if kind == "control":
            out[kind] = {"n": len(rows), "llm_accepted": accepted_llm, "final_accepted": accepted_final}
        else:
            out[kind] = {"n": len(rows), "llm_caught": len(rows) - accepted_llm,
                         "final_caught": len(rows) - accepted_final}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-type", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    init_db()
    run_ids = metrics.done_run_ids() or [store.latest_run_id()]
    pool = [(rid, c) for rid in run_ids for c in store.list_claims(rid, verdict="supported")]
    chunks_by_run = {rid: store.list_evidence(rid) for rid in run_ids}
    cases = build_cases(pool, chunks_by_run, args.per_type, args.seed, allowed_numbers_for)
    print(f"{len(cases)} test claims built from {len(pool)} verified claims in {len(run_ids)} run(s)")

    with ThreadPoolExecutor(max_workers=settings.verifier_workers) as ex:
        results = list(ex.map(evaluate, cases))
    errors = [r for r in results if "error" in r]
    if len(errors) == len(results):
        raise SystemExit(f"Every call failed, for example: {errors[0]['error']}")

    s = stats(results)
    missed = [r for r in results if r["expected"] == "reject" and r.get("final_verdict") == "supported"]
    OUT.write_text(json.dumps({
        "meta": {"date": date.today().isoformat(), "verifier_model": settings.verifier_model,
                 "runs": len(run_ids), "per_type": args.per_type, "errors": len(errors)},
        "stats": s, "missed": missed[:12]}, indent=2), encoding="utf-8")

    for kind, v in s.items():
        if kind == "control":
            print(f"control      n={v['n']:3}  kept by LLM {v['llm_accepted']}/{v['n']}  kept after code {v['final_accepted']}/{v['n']}")
        else:
            print(f"{kind:12} n={v['n']:3}  caught by LLM alone {v['llm_caught']}/{v['n']}  by LLM + code {v['final_caught']}/{v['n']}")
    print(f"{len(missed)} false claims slipped through (saved in {OUT.name}). Wrote {report.build()}")


if __name__ == "__main__":
    main()