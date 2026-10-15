import re
from pathlib import Path
from agents.drafter import too_similar
from pydantic import BaseModel, Field, ValidationError
from textcheck import coverage
import trail
from agents.prompts import SYNTHESIZER_SYSTEM
from agents.verifier import numbers
from config import settings
from coverage import thin_subquestions
from db import store
from llm import chat_json
from models import Claim, SubQuestion

CITE = re.compile(r"\[(C\d+)\]")
RUN = re.compile(r"(?:\[C\d+\])+")
SEGMENT = re.compile(r"(.+?)((?:\[C\d+\])+)\s*([.!?]?)", re.S)


class Conflict(BaseModel):
    claims: list[str] = Field(min_length=2)
    note: str = ""


class SectionOut(BaseModel):
    paragraphs: list[str] = Field(min_length=1)
    conflicts: list[Conflict] = Field(default_factory=list)


# ---------- code checks on the model's prose ----------

def check_paragraph(text: str, allowed: set[str], by_id: dict[str, Claim],
                    extra_numbers: set[str]) -> str | None:
    """Return a reason the paragraph must be dropped, or None if it is acceptable."""
    ids = CITE.findall(text)
    if not ids:
        return "paragraph has no citation"
    unknown = [i for i in ids if i not in allowed]
    if unknown:
        return f"cites claims that were not provided: {unknown}"
    ok_numbers = set(extra_numbers)
    for i in ids:
        ok_numbers |= numbers(by_id[i].text)
    invented = numbers(CITE.sub("", text)) - ok_numbers
    if invented:
        return f"contains numbers not found in the cited claims: {sorted(invented)}"
    return None


# ---------- citation rendering (the model never writes URLs) ----------

class Sources:
    def __init__(self, run_id: str):
        self.chunks = {c.id: c for c in store.list_evidence(run_id)}
        self.urls: list[str] = []
        self.meta: dict[str, object] = {}

    def numbers_for(self, claim: Claim) -> list[tuple[int, str]]:
        out = []
        for eid in claim.evidence_ids:
            chunk = self.chunks.get(eid)
            if not chunk:
                continue
            if chunk.url not in self.urls:
                self.urls.append(chunk.url)
                self.meta[chunk.url] = chunk
            pair = (self.urls.index(chunk.url) + 1, chunk.url)
            if pair not in out:
                out.append(pair)
        return out

    def link(self, claim: Claim) -> str:
        return "".join(f"[[{n}]]({url})" for n, url in self.numbers_for(claim))

    def cite(self, text: str, by_id: dict[str, Claim]) -> str:
        def repl(m: re.Match) -> str:
            pairs: list[tuple[int, str]] = []
            for cid in CITE.findall(m.group(0)):
                for pair in self.numbers_for(by_id[cid]):
                    if pair not in pairs:
                        pairs.append(pair)
            return "".join(f"[[{n}]]({url})" for n, url in pairs)
        return RUN.sub(repl, text)

    def render_list(self) -> str:
        lines = []
        for i, url in enumerate(self.urls, 1):
            c = self.meta[url]
            lines.append(f"{i}. {c.title or c.domain} — {c.domain} (trust {c.tier}) — {url}")
        return "\n".join(lines)


# ---------- one section ----------

def write_section(run_id: str, question: str, sq: SubQuestion, claims: list[Claim]):
    """Returns (paragraphs, conflicts, used_fallback). Never raises: the fallback is always available."""
    by_id = {c.id: c for c in claims}
    allowed = set(by_id)
    extra_numbers = numbers(question) | numbers(sq.text)
    listing = "\n".join(f"[{c.id}] {c.text}" for c in claims)
    messages = [
        {"role": "system", "content": SYNTHESIZER_SYSTEM},
        {"role": "user", "content": f"Sub-question: {sq.text}\n\nVerified claims:\n{listing}"},
    ]

    paragraphs: list[str] = []
    conflicts: list[tuple[list[str], str]] = []
    try:
        out = SectionOut.model_validate(chat_json(messages, settings.synthesizer_model, temperature=0.2))
    except Exception as e:                                   # API error, bad JSON, bad shape
        trail.log(run_id, "synthesizer", "synthesis_failed",
                  reason=f"{type(e).__name__}: {e}"[:300], sub_q=sq.id)
        out = None

    if out:
        ignore_text = f"{question} {sq.text}"
        for p in out.paragraphs:
            p = " ".join(p.split())
            problem = check_paragraph(p, allowed, by_id, extra_numbers)
            if problem:
                trail.log(run_id, "synthesizer", "paragraph_dropped",
                          reason=problem, sub_q=sq.id, paragraph=p[:300])
                continue
            grounded, changed = ground_sentences(p, by_id, ignore_text)
            if changed:
                trail.log(run_id, "synthesizer", "sentences_grounded",
                          reason="sentence went beyond its cited claims; replaced by the verified claim text",
                          sub_q=sq.id, changed=changed, original=p[:400])
            if grounded:
                paragraphs.append(grounded)
        for c in out.conflicts:
            ids = list(dict.fromkeys(c.claims))
            if len(ids) >= 2 and all(i in allowed for i in ids):
                conflicts.append((ids, c.note.strip()))

    if paragraphs:
        return paragraphs, conflicts, False

    bullets = "\n".join(f"- {c.text} [{c.id}]" for c in claims)
    return [bullets], conflicts, True


# ---------- the report ----------

def synthesize(run_id: str) -> str:
    run = store.get_run(run_id)
    question = run["question"]
    subqs = store.list_sub_questions(run_id)
    all_claims = store.list_claims(run_id)
    supported = [c for c in all_claims if c.verdict == "supported"]
    kept: list[Claim] = []
    for c in supported:
        if too_similar(c.text, [k.text for k in kept]):
            trail.log(run_id, "synthesizer", "duplicate_claim_skipped", claim_id=c.id,
                      reason="near-identical to an earlier verified claim")
        else:
            kept.append(c)
    supported = kept
    thin = thin_subquestions(run_id)
    sources = Sources(run_id)

    sections, conflict_lines = [], []
    for sq in subqs:
        claims = [c for c in supported if c.sub_q_id == sq.id]
        if not claims:
            continue
        by_id = {c.id: c for c in claims}
        paragraphs, conflicts, fallback = write_section(run_id, question, sq, claims)
        sections.append(f"## {sq.text}\n\n" + "\n\n".join(sources.cite(p, by_id) for p in paragraphs))
        for ids, note in conflicts:
            quoted = "; ".join(f'"{by_id[i].text}" {sources.link(by_id[i])}' for i in ids)
            conflict_lines.append(f"- **{sq.text}** {note or 'These claims disagree.'} {quoted}")
        trail.log(run_id, "synthesizer", "section_written",
                  reason="fallback bullet list used" if fallback else "model prose passed all checks",
                  sub_q=sq.id, claims=len(claims), fallback=fallback, conflicts=len(conflicts))

    counts = {v: sum(1 for c in all_claims if c.verdict == v)
              for v in ("supported", "partial", "unsupported", "contradicted", "pending")}
    total = len(all_claims)
    parts = [
        f"# {question}",
        f"**{counts['supported']} of {total} drafted claims passed verification** "
        f"({counts['partial']} partially supported, {counts['unsupported']} unsupported, "
        f"{counts['contradicted']} contradicted, {counts['pending']} unchecked). "
        "Only verified claims appear in the report; the rest are listed in Appendix B.",
        *sections,
    ]
    if conflict_lines:
        parts.append("## Where sources disagree\n\n" + "\n".join(conflict_lines))

    gaps = [f"- **{sq.text}** — {thin[sq.id]['claims']} verified claim(s) from "
            f"{thin[sq.id]['domains']} source domain(s); not enough to draw firm conclusions."
            for sq in subqs if sq.id in thin]
    if gaps:
        parts.append("## Areas with insufficient evidence\n\n" + "\n".join(gaps))

    appendix_a = []
    for c in supported:
        nums = " ".join(f"[{n}]" for n, _ in sources.numbers_for(c))
        appendix_a.append(f'- {c.text} {nums}\n  > "{c.quote}"')

    rejected = [c for c in all_claims if c.verdict != "supported"]
    appendix_b = [f"- {c.text} — *{c.verdict}*: {c.reason or 'not checked'}" for c in rejected]

    parts.append("## Sources\n\n" + sources.render_list())
    parts.append("## Appendix A: verified claims and their supporting quotes\n\n" + "\n".join(appendix_a))
    parts.append("## Appendix B: claims that were not verified\n\n" + ("\n".join(appendix_b) or "None."))

    report = "\n\n".join(parts) + "\n"
    store.update_run(run_id, report_md=report)
    trail.log(run_id, "synthesizer", "report_written",
              reason=f"{counts['supported']} verified claims, {len(sources.urls)} sources",
              verified=counts["supported"], total=total, thin=list(thin))
    return report


def ground_sentences(text: str, by_id: dict[str, Claim], ignore_text: str = "") -> tuple[str, int]:
    """Each citation-ended sentence must stay close to the claims it cites. If not, replace it with
    those claims' own text. Trailing text with no citation is dropped. Returns (text, changes)."""
    out, changed, last, used = [], 0, 0, set()
    for m in SEGMENT.finditer(text):
        last = m.end()
        seg, cites, punct = m.group(1).strip(), m.group(2), m.group(3) or "."
        if not seg:
            continue
        ids = tuple(dict.fromkeys(CITE.findall(cites)))
        reference = " ".join(by_id[i].text for i in ids)
        if coverage(seg, reference, ignore_text) >= settings.min_sentence_coverage:
            out.append(f"{seg} {cites}{punct}")
        else:
            changed += 1
            if ids not in used:                       # don't repeat the same fact twice
                facts = "; ".join(by_id[i].text.rstrip(" .!?") for i in ids)
                out.append(f"{facts} {cites}.")
        used.add(ids)
    if text[last:].strip(" .!?\n"):
        changed += 1
    return " ".join(out), changed

if __name__ == "__main__":
    # uv run python -m agents.synthesizer            (latest run)
    import sys
    from db.connection import init_db

    init_db()
    rid = sys.argv[1] if len(sys.argv) > 1 else store.latest_run_id()
    text = synthesize(rid)
    out = Path("data") / f"report_{rid}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nSaved to {out}")