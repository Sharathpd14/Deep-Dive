"""End-to-end benchmark.
  uv run python -m eval.run_eval --limit 1        pilot run: check cost and time first
  uv run python -m eval.run_eval                  everything not yet done
  uv run python -m eval.run_eval --only q03 --redo
"""
import argparse
import json
import time
from datetime import date
from pathlib import Path

import llm
from agents.verifier import out_of_credit
from config import settings
from db import store
from db.connection import init_db
from eval import metrics, report
from graph import run_pipeline

QUESTIONS = Path(__file__).with_name("questions.json")


def load_results() -> dict:
    if metrics.RESULTS.exists():
        return json.loads(metrics.RESULTS.read_text(encoding="utf-8"))
    return {"meta": {}, "questions": {}}


def save_results(data: dict) -> None:
    metrics.RESULTS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def current_meta() -> dict:
    keys = ["planner_model", "drafter_model", "verifier_model", "synthesizer_model", "cheap_model",
            "max_loops", "min_verified_claims", "min_distinct_domains",
            "min_quote_coverage", "min_sentence_coverage"]
    return {"date": date.today().isoformat(), **{k: getattr(settings, k) for k in keys}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--pause", type=int, default=20, help="seconds between questions (search politeness)")
    args = ap.parse_args()

    init_db()
    data = load_results()
    data["meta"] = current_meta()
    started = 0

    for q in json.loads(QUESTIONS.read_text(encoding="utf-8")):
        if args.only and q["id"] not in args.only:
            continue
        if not args.redo and data["questions"].get(q["id"], {}).get("status") == "done":
            continue
        if args.limit is not None and started >= args.limit:
            break
        started += 1

        print(f"\n[{q['id']}] {q['question']}")
        run_id = store.create_run(q["question"])
        before, t0 = dict(llm.USAGE), time.time()
        try:
            run_pipeline(run_id=run_id)
            rec = metrics.collect(run_id)
            rec["status"] = "incomplete" if rec["pending"] or out_of_credit() else "done"
        except Exception as e: # noqa: BLE001
            rec = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}

        rec.update(id=q["id"], question=q["question"], domain=q["domain"], kind=q["kind"],
                   run_id=run_id, wall_seconds=round(time.time() - t0, 1),
                   llm_calls=llm.USAGE["calls"] - before["calls"],
                   tokens=llm.USAGE["tokens"] - before["tokens"])
        data["questions"][q["id"]] = rec
        save_results(data)

        if rec["status"] == "done":
            print(f"  {rec['supported']}/{rec['drafted']} claims verified ({rec['pass_rate']:.0%}), "
                  f"loops={rec['loops']}, thin {rec['thin_initial']}->{rec['thin_final']}, "
                  f"{rec['llm_calls']} calls, {rec['tokens']:,} tokens, {rec['wall_seconds']}s")
        else:
            print(f"  {rec['status']}: {rec.get('error', 'claims left unchecked')}")
            if "402" in rec.get("error", "") or out_of_credit():
                print("  Mesh balance exhausted. Top up, then re-run: finished questions are skipped.")
                break
        time.sleep(args.pause)

    print(f"\nWrote {report.build()}")


if __name__ == "__main__":
    main()