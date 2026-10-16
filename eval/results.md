# Evaluation results

Run on 2026-10-16. Models: planner `openai/gpt-4o-mini`, drafter `openai/gpt-4o-mini`, verifier `openai/gpt-4.1-mini`, synthesizer `openai/gpt-4.1-mini`. Quote coverage threshold 0.7, sentence coverage 0.85, max 2 research loops.

## 1. End-to-end benchmark

| Question | Domain | Drafted | Verified | Pass rate | Loops | Thin sub-questions (start to end) | Sources cited | Avg trust | Tokens |
|---|---|---|---|---|---|---|---|---|---|
| q01 | finance | 35 | 24 | 69% | 1 | 1 to 0 of 5 | 11 | 0.58 | 75k |
| q02 | health | 44 | 37 | 84% | 2 | 2 to 1 of 6 | 14 | 0.79 | 109k |
| q03 | policy | 34 | 31 | 91% | 1 | 1 to 0 of 5 | 12 | 0.75 | 68k |
| q04 | climate | 33 | 30 | 91% | 1 | 1 to 0 of 5 | 12 | 0.58 | 67k |
| q05 | energy | 40 | 33 | 82% | 1 | 1 to 0 of 6 | 16 | 0.80 | 78k |
| q06 | economics | 47 | 34 | 72% | 1 | 2 to 0 of 6 | 14 | 0.57 | 95k |
| q07 | technology | 35 | 31 | 89% | 1 | 1 to 0 of 5 | 14 | 0.71 | 73k |
| q08 | work | 29 | 28 | 97% | 0 | 0 to 0 of 5 | 12 | 0.62 | 58k |
| q09 | space | 38 | 29 | 76% | 1 | 4 to 2 of 6 | 13 | 0.62 | 89k |
| q10 | health | 28 | 23 | 82% | 0 | 0 to 0 of 5 | 13 | 0.75 | 61k |
| q11 | economics | 40 | 35 | 88% | 1 | 2 to 1 of 6 | 13 | 0.73 | 94k |
| q12 | agriculture | 42 | 26 | 62% | 2 | 2 to 0 of 5 | 11 | 0.55 | 86k |

**12 questions. 361 of 445 drafted claims verified (81% overall, 82% averaged per question).**

- Coverage: the share of sub-questions with enough verified evidence (at least 2 claims from 2+ domains) went from **74% after the first pass to 94% after the research loop** (mean 1.0 loops per question).
- The loop contributed 61 of the 361 verified claims.
- Sources behind verified claims: 12.9 per question on average, average trust score 0.67, 35% from high-trust domains (score 0.8 or more).
- Source disagreements flagged in reports: 0.
- Cost per question: about 55 LLM calls, 80k tokens, 4.9 minutes.

## 2. Against an unverified baseline

A tool that publishes every drafted claim would have published 445 claims, and only **81%** of them are supported by the source they cite. DeepDive kept 361 and removed or downgraded 84. In 62 of those cases the model had said "supported" and the code checks overruled it.

## 3. Adversarial test of the verifier

Verifier model: `openai/gpt-4.1-mini`. Test claims were built from verified claims in 12 runs.

| Planted fault | n | Caught by LLM alone | Caught by LLM + code checks |
|---|---|---|---|
| A figure changed | 20 | 85% | 100% |
| Meaning reversed | 20 | 95% | 95% |
| Unsupported cause or forecast added | 20 | 100% | 100% |
| True claim, wrong source | 20 | 100% | 100% |
| **All planted faults** | 80 | **95%** | **99%** |

Control (correct claims left untouched): 100% still accepted after all checks (20 of 20). Every extra check can cost some true claims, and this is that cost.

Faults that got through (see `adversarial.json`):
- *reversal*: "Reliance on DAC could increase the urgency of reducing emissions at the source."

## 4. Blind human spot check

_Not run yet._

## 5. Limitations

- The verifier is an LLM plus heuristic code checks, not a proof. Checks catch fabricated quotes, wrong numbers and claims wider than their quote, but a supported claim can still go slightly beyond its quote.
- The planted faults are synthetic and fairly blunt. Subtle real-world errors are harder than these.
- Web search results change from day to day, so a re-run will give different numbers.
- The spot check is small, so read the confidence intervals, not just the percentages.
- Report prose is checked sentence by sentence against its cited claims with a word-overlap heuristic, not a full entailment check.
