"""Blind human check of the verifier.
  uv run python -m eval.spot_check --sample 40     writes eval/spot_check.csv
  (open it, fill the human_verdict column, save)
  uv run python -m eval.spot_check --score         computes agreement
"""
import argparse
import csv
import json
import math
import random
from pathlib import Path

from db import store
from db.connection import init_db
from eval import metrics, report

HERE = Path(__file__).parent
CSV_PATH, KEY_PATH, SCORES = HERE / "spot_check.csv", HERE / "spot_check_key.json", HERE / "spot_check_scores.json"
FIELDS = ["id", "claim", "source_url", "source_text", "human_verdict", "notes"]
GUIDE = """Fill human_verdict with one of:
  yes      the source text clearly states everything in the claim
  partial  the source states part of it, or the claim says more than the source does
  no       the source does not support the claim
Judge ONLY from source_text. Do not use your own knowledge."""


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p, d = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def sample(n: int = 40, seed: int = 11) -> None:
    ok, bad = [], []
    for rid in metrics.done_run_ids():
        for c in store.list_claims(rid):
            if c.verdict == "supported":
                ok.append((rid, c))
            elif c.verdict != "pending":
                bad.append((rid, c))
    rng = random.Random(seed)
    picks = rng.sample(ok, min(n // 2, len(ok))) + rng.sample(bad, min(n - n // 2, len(bad)))
    rng.shuffle(picks)

    rows, key = [], {}
    for i, (rid, c) in enumerate(picks, 1):
        chunks = store.get_chunks(rid, c.evidence_ids)
        sid = f"s{i:02d}"
        rows.append({"id": sid, "claim": c.text, "source_url": " | ".join(ch.url for ch in chunks),
                     "source_text": "\n---\n".join(ch.text[:2500] for ch in chunks),
                     "human_verdict": "", "notes": ""})
        key[sid] = {"run_id": rid, "claim_id": c.id, "system_verdict": c.verdict}

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    KEY_PATH.write_text(json.dumps(key, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} claims to {CSV_PATH}\n\n{GUIDE}\n\nThen run: uv run python -m eval.spot_check --score")


def score(csv_path: Path = CSV_PATH, key_path: Path = KEY_PATH) -> dict:
    key = json.loads(key_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        labels = {r["id"]: r["human_verdict"].strip().lower() for r in csv.DictReader(f)}
    labels = {k: v for k, v in labels.items() if v in ("yes", "partial", "no") and k in key}

    kept = [v for k, v in labels.items() if key[k]["system_verdict"] == "supported"]
    rejected = [v for k, v in labels.items() if key[k]["system_verdict"] != "supported"]
    out = {
        "labeled": len(labels),
        "kept_n": len(kept), "kept_yes": kept.count("yes"), "kept_partial": kept.count("partial"),
        "kept_no": kept.count("no"),
        "rejected_n": len(rejected), "rejected_yes": rejected.count("yes"),
        "rejected_partial": rejected.count("partial"), "rejected_no": rejected.count("no"),
    }
    out["precision"] = out["kept_yes"] / len(kept) if kept else None
    out["precision_ci"] = wilson(out["kept_yes"], len(kept))
    out["lenient_precision"] = (out["kept_yes"] + out["kept_partial"]) / len(kept) if kept else None
    out["false_reject_rate"] = out["rejected_yes"] / len(rejected) if rejected else None
    out["false_reject_ci"] = wilson(out["rejected_yes"], len(rejected))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int)
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    init_db()
    if args.sample:
        sample(args.sample)
    elif args.score:
        s = score()
        SCORES.write_text(json.dumps(s, indent=2), encoding="utf-8")
        print(json.dumps(s, indent=2))
        print(f"Wrote {report.build()}")
    else:
        ap.print_help()