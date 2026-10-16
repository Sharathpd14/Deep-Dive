
import json
from pathlib import Path

from eval import metrics

HERE = Path(__file__).parent
ADV, SPOT, OUT = HERE / "adversarial.json", HERE / "spot_check_scores.json", HERE / "results.md"
LABELS = {
    "number_swap": "A figure changed",
    "reversal": "Meaning reversed",
    "overreach": "Unsupported cause or forecast added",
    "wrong_source": "True claim, wrong source",
}
NOT_RUN = "_Not run yet._"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _ci(p, ci) -> str:
    return "n/a" if p is None else f"{p:.0%} (95% CI {ci[0]:.0%} to {ci[1]:.0%})"


def _end_to_end(res) -> list[str]:
    rows = sorted(
        (
            r
            for r in (res or {"questions": {}})["questions"].values()
            if r.get("status") == "done"
        ),
        key=lambda r: r["id"],
    )
    if not rows:
        return [NOT_RUN, ""]

    s = metrics.summarize(rows)
    out = [
        "| Question | Domain | Drafted | Verified | Pass rate | Loops | Thin sub-questions (start to end) | Sources cited | Avg trust | Tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in rows:
        out.append(
            f"| {r['id']} | {r['domain']} | {r['drafted']} | {r['supported']} | {pct(r['pass_rate'])} | "
            f"{r['loops']} | {r['thin_initial']} to {r['thin_final']} of {r['sub_questions']} | "
            f"{r['sources_cited']} | {r['mean_tier']:.2f} | {r.get('tokens', 0) // 1000}k |"
        )

    others = [
        r for r in res["questions"].values()
        if r.get("status") != "done"
    ]

    out += [
        "",
        (
            f"**{s['questions']} questions. {s['supported']} of {s['drafted']} drafted claims verified "
            f"({pct(s['micro_pass_rate'])} overall, {pct(s['macro_pass_rate'])} averaged per question).**"
        ),
        "",
        (
            f"- Coverage: the share of sub-questions with enough verified evidence (at least "
            f"{res['meta']['min_verified_claims']} claims from {res['meta']['min_distinct_domains']}+ domains) went from "
            f"**{pct(s['completeness_initial'])} after the first pass to {pct(s['completeness_final'])} after the research loop** "
            f"(mean {s['mean_loops']:.1f} loops per question)."
        ),
        f"- The loop contributed {s['supported'] - s['round0_supported']} of the {s['supported']} verified claims.",
        (
            f"- Sources behind verified claims: {s['mean_sources_cited']:.1f} per question on average, "
            f"average trust score {s['mean_tier']:.2f}, {pct(s['primary_share'])} from high-trust domains (score 0.8 or more)."
        ),
        f"- Source disagreements flagged in reports: {s['conflicts_flagged']}.",
        (
            f"- Cost per question: about {s['mean_calls']:.0f} LLM calls, {s['mean_tokens'] / 1000:.0f}k tokens, "
            f"{s['mean_seconds'] / 60:.1f} minutes."
        ),
    ]

    if others:
        out += [
            "",
            "Not counted: " + ", ".join(
                f"{r['id']} ({r['status']})" for r in others
            ),
        ]

    return out + [""]


def _baseline(res) -> list[str]:
    s = metrics.summarize(
        [r for r in (res or {"questions": {}})["questions"].values()]
    )
    if not s:
        return [NOT_RUN, ""]

    return [
        (
            f"A tool that publishes every drafted claim would have published {s['drafted']} claims, and only "
            f"**{pct(s['micro_pass_rate'])}** of them are supported by the source they cite. DeepDive kept "
            f"{s['supported']} and removed or downgraded {s['removed']}. In {s['code_overrides']} of those cases the "
            f"model had said \"supported\" and the code checks overruled it."
        ),
        "",
    ]


def _adversarial(adv) -> list[str]:
    if not adv:
        return [NOT_RUN, ""]

    st, m = adv["stats"], adv["meta"]
    out = [
        f"Verifier model: `{m['verifier_model']}`. Test claims were built from verified claims in {m['runs']} runs.",
        "",
        "| Planted fault | n | Caught by LLM alone | Caught by LLM + code checks |",
        "|---|---|---|---|",
    ]

    tn = tl = tf = 0

    for kind, label in LABELS.items():
        if kind in st:
            v = st[kind]
            tn, tl, tf = tn + v["n"], tl + v["llm_caught"], tf + v["final_caught"]
            out.append(
                f"| {label} | {v['n']} | {pct(v['llm_caught'] / v['n'])} | {pct(v['final_caught'] / v['n'])} |"
            )

    if tn:
        out.append(
            f"| **All planted faults** | {tn} | **{pct(tl / tn)}** | **{pct(tf / tn)}** |"
        )

    if "control" in st:
        c = st["control"]
        out += [
            "",
            (
                f"Control (correct claims left untouched): {pct(c['final_accepted'] / c['n'])} still accepted "
                f"after all checks ({c['final_accepted']} of {c['n']}). Every extra check can cost some true claims, and this is that cost."
            ),
        ]

    if adv.get("missed"):
        out += [
            "",
            "Faults that got through (see `adversarial.json`):",
        ] + [
            f"- *{r['kind']}*: \"{r['text']}\""
            for r in adv["missed"][:5]
        ]

    return out + [""]


def _spot(sp) -> list[str]:
    if not sp or not sp.get("labeled"):
        return [NOT_RUN, ""]

    return [
        f"{sp['labeled']} claims labeled blind from the source text alone.",
        "",
        (
            f"- **Precision of claims the system kept:** {_ci(sp['precision'], sp['precision_ci'])} fully supported "
            f"({sp['kept_yes']} of {sp['kept_n']}); {pct(sp['lenient_precision'])} if partial support counts."
        ),
        (
            f"- **False rejections:** {_ci(sp['false_reject_rate'], sp['false_reject_ci'])} of the claims the system rejected "
            f"were in fact fully supported ({sp['rejected_yes']} of {sp['rejected_n']})."
        ),
        "",
    ]


LIMITS = [
    (
        "- The verifier is an LLM plus heuristic code checks, not a proof. Checks catch fabricated quotes, wrong numbers and "
        "claims wider than their quote, but a supported claim can still go slightly beyond its quote."
    ),
    "- The planted faults are synthetic and fairly blunt. Subtle real-world errors are harder than these.",
    "- Web search results change from day to day, so a re-run will give different numbers.",
    "- The spot check is small, so read the confidence intervals, not just the percentages.",
    "- Report prose is checked sentence by sentence against its cited claims with a word-overlap heuristic, not a full entailment check.",
]


def build() -> Path:
    res, adv, sp = _load(metrics.RESULTS), _load(ADV), _load(SPOT)
    meta = (res or {}).get("meta", {})
    lines = ["# Evaluation results", ""]

    if meta:
        lines += [
            (
                f"Run on {meta['date']}. Models: planner `{meta['planner_model']}`, drafter `{meta['drafter_model']}`, "
                f"verifier `{meta['verifier_model']}`, synthesizer `{meta['synthesizer_model']}`. "
                f"Quote coverage threshold {meta['min_quote_coverage']}, sentence coverage {meta['min_sentence_coverage']}, "
                f"max {meta['max_loops']} research loops."
            ),
            "",
        ]

    lines += [
        "## 1. End-to-end benchmark",
        "",
        *_end_to_end(res),
        "## 2. Against an unverified baseline",
        "",
        *_baseline(res),
        "## 3. Adversarial test of the verifier",
        "",
        *_adversarial(adv),
        "## 4. Blind human spot check",
        "",
        *_spot(sp),
        "## 5. Limitations",
        "",
        *LIMITS,
        "",
    ]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    return OUT
