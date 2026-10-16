"""Write docs/rejected-claim-example.md from a real claim in your database that the model
called "supported" and the code checks rejected.

  uv run python scripts/rejected_example.py            first match
  uv run python scripts/rejected_example.py --list     show all matches
  uv run python scripts/rejected_example.py --index 3  use the 4th match
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import store  # noqa: E402
from db.connection import get_conn, init_db  # noqa: E402
from textcheck import coverage  # noqa: E402

OUT = ROOT / "docs" / "rejected-claim-example.md"
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def candidates() -> list[tuple[str, object]]:
    """Rejections where the model said 'supported' and code overruled it with 'quote not found'."""
    with get_conn() as conn:
        run_ids = [r["id"] for r in conn.execute("SELECT id FROM runs ORDER BY created_at")]
    found = []
    for run_id in run_ids:
        for e in store.list_events(run_id):
            if (e.action == "claim_rejected" and e.detail.get("code_override")
                    and e.detail.get("llm_verdict") == "supported"
                    and e.reason.startswith("quote not found")):
                found.append((run_id, e))
    return found


def closest_passages(claim_text: str, chunks, n: int = 3) -> list[str]:
    sentences = [s.strip() for c in chunks for s in SENTENCE.split(c.text) if len(s.strip()) > 30]
    return sorted(sentences, key=lambda s: -coverage(claim_text, s))[:n]


def write_example(run_id: str, event) -> Path:
    claim = next(c for c in store.list_claims(run_id) if c.id == event.detail["claim_id"])
    chunks = store.get_chunks(run_id, claim.evidence_ids)
    sources = "\n".join(f"- {c.title or c.domain} ({c.url})" for c in chunks)
    passages = "\n".join(f"> {p}" for p in closest_passages(claim.text, chunks))
    verified = next((c for c in store.list_claims(run_id, verdict="supported") if c.quote), None)

    parts = [
        "### A claim the model accepted and the code rejected",
        f"**Claim written by the drafter:**\n\n> {claim.text}",
        f"**Cited source:**\n\n{sources}",
        "**What the verifier model said:** *supported*, and it returned a quote.",
        f"**What the code found:**\n\n```\n{event.reason}\n```\n\nThe quote the model returned does not appear "
        "in the cited text, so the claim was marked unsupported and left out of the report. "
        "The trail records this as a `code_override`.",
        f"**The closest passages in the cited source:**\n\n{passages}",
    ]
    if verified:
        parts.append("**For contrast, a claim that passed.** Its quote is copied word for word from the source:\n\n"
                     f"> {verified.text}\n\n*Supporting quote:* \"{verified.quote}\"")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return OUT


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--index", type=int, default=0)
    args = ap.parse_args()

    init_db()
    found = candidates()
    if not found:
        raise SystemExit("No code-overridden rejections found yet. Run a few questions first.")
    if args.list:
        for i, (rid, e) in enumerate(found):
            claim = next(c for c in store.list_claims(rid) if c.id == e.detail["claim_id"])
            print(f"[{i}] run {rid} {claim.id}: {claim.text[:110]}")
        return
    run_id, event = found[min(args.index, len(found) - 1)]
    print(f"Wrote {write_example(run_id, event)}  ({len(found)} candidates; use --list to pick another)")


if __name__ == "__main__":
    main()