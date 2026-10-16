import csv
import random

import pytest

from db import connection, store
from eval import adversarial, metrics, report, spot_check
from models import Chunk, Claim


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


def _claim(text, evidence=("E1",)):
    return Claim(id="C1", run_id="r", sub_q_id="Q1", text=text, evidence_ids=list(evidence))


def _chunk(cid, text):
    return Chunk(id=cid, run_id="r", url=f"https://x.com/{cid}", text=text)


# ---------- metrics ----------

def _seed():
    rid = store.create_run("What are EV risks in 2026?")
    store.add_sub_question(rid, "What are the risks?", ["q"])
    store.add_sub_question(rid, "Who are the players?", ["q"])
    store.add_evidence(rid, "Q1", "https://www.iea.org/a", "", "Risk text one " * 10, 1.0)        # E1
    store.add_evidence(rid, "Q1", "https://blog.example.com/b", "", "Blog text two " * 10, 0.5)   # E2
    store.add_evidence(rid, "Q2", "https://www.reuters.com/c", "", "Players text three " * 10, 0.8)  # E3
    store.add_evidence(rid, "Q2", "https://other.example.com/d", "", "Players text four " * 10, 0.5)  # E4
    plan = [("Q1", "E1", "supported", 0), ("Q2", "E3", "supported", 1), ("Q2", "E4", "supported", 1),
            ("Q1", "E2", "partial", 0), ("Q1", "E2", "unsupported", 0)]
    for i, (sq, ev, verdict, rnd) in enumerate(plan, 1):
        c = store.save_claim(rid, sq, f"Claim number {i} about the topic.", [ev], rnd)
        store.set_claim_verdict(rid, c.id, verdict, "q", "r")
    store.log_event(rid, "verifier", "claim_rejected", detail={"code_override": True})
    store.log_event(rid, "verifier", "claim_rejected", detail={"code_override": False})
    store.log_event(rid, "coordinator", "loop_back", detail={"targets": ["Q1", "Q2"]})
    store.update_run(rid, loop_count=1, status="done")
    return rid


def test_collect_counts_claims_loops_and_sources():
    m = metrics.collect(_seed())
    assert (m["drafted"], m["supported"], m["partial"], m["unsupported"]) == (5, 3, 1, 1)
    assert m["pass_rate"] == pytest.approx(0.6)
    assert m["code_overrides"] == 1
    assert (m["thin_initial"], m["thin_final"]) == (2, 1)        # Q1 has 1 verified claim: still thin
    assert m["round0_supported"] == 1 and m["loops"] == 1
    assert m["sources_cited"] == 3
    assert m["mean_tier"] == pytest.approx((1.0 + 0.8 + 0.5) / 3)
    assert m["primary_share"] == pytest.approx(2 / 3)


def test_summarize_ignores_unfinished_questions():
    row = {"status": "done", **metrics.collect(_seed()), "llm_calls": 10, "tokens": 1000}
    s = metrics.summarize([row, {"status": "failed"}])
    assert s["questions"] == 1 and s["micro_pass_rate"] == pytest.approx(0.6)
    assert s["completeness_initial"] == 0.0 and s["completeness_final"] == pytest.approx(0.5)


# ---------- planted faults ----------

def test_swap_number_changes_figures_but_not_forbidden_ones():
    out = adversarial.swap_number("Demand reached 950 GWh in 2024.", {"2024"})
    assert "2024" in out and "950" not in out
    assert "2027" in adversarial.swap_number("Sales rose in 2024.")
    assert adversarial.swap_number("Things changed in 2026.", {"2026"}) is None
    assert "3,600" in adversarial.swap_number("It cost 1,200 dollars.")


def test_flip_meaning():
    assert adversarial.flip_meaning("Prices rose in 2024.") == "Prices fell in 2024."
    assert adversarial.flip_meaning("Cells came from Asia.") is None


def test_overreach_adds_a_clause_without_digits():
    out = adversarial.add_overreach("Demand rose in the region.", random.Random(1))
    assert out.startswith("Demand rose in the region") and out.endswith(".")
    assert not any(ch.isdigit() for ch in out)


def test_unrelated_chunk_is_chosen_for_wrong_source():
    claim = _claim("Battery demand reached new records this year.", ["E1"])
    chunks = [_chunk("E1", "Battery demand reached new records this year."),
              _chunk("E2", "Battery demand reached new heights in the market."),
              _chunk("E3", "Cobalt mining in Congo employs thousands of workers.")]
    assert adversarial.pick_unrelated_chunk(claim, chunks, random.Random(1)).id == "E3"
    assert adversarial.pick_unrelated_chunk(claim, chunks[:2], random.Random(1)) is None


def test_build_cases_makes_each_kind_up_to_the_cap():
    pool = [("r", Claim(id=f"C{i}", run_id="r", sub_q_id="Q1", evidence_ids=["E1"],
                        text="Battery demand rose to 950 gigawatt hours across Asian markets."))
            for i in range(4)]
    chunks = {"r": [_chunk("E1", "Battery demand rose to 950 gigawatt hours across Asian markets."),
                    _chunk("E2", "Cobalt mining in Congo employs thousands of workers locally.")]}
    cases = adversarial.build_cases(pool, chunks, per_type=2, seed=1)
    kinds = [c["kind"] for c in cases]
    assert {k: kinds.count(k) for k in set(kinds)} == {
        "control": 2, "number_swap": 2, "reversal": 2, "overreach": 2, "wrong_source": 2}
    assert all(c["expected"] == ("accept" if c["kind"] == "control" else "reject") for c in cases)
    wrong = next(c for c in cases if c["kind"] == "wrong_source")
    assert wrong["evidence_ids"] == ["E2"] and wrong["text"] == wrong["original"]


def test_stats_separate_the_llm_from_the_code_layer():
    rows = [{"kind": "number_swap", "llm_verdict": "supported", "final_verdict": "partial"},
            {"kind": "number_swap", "llm_verdict": "unsupported", "final_verdict": "unsupported"},
            {"kind": "control", "llm_verdict": "supported", "final_verdict": "supported"},
            {"kind": "control", "llm_verdict": "supported", "final_verdict": "partial"}]
    s = adversarial.stats(rows)
    assert s["number_swap"] == {"n": 2, "llm_caught": 1, "final_caught": 2}
    assert s["control"] == {"n": 2, "llm_accepted": 2, "final_accepted": 1}


# ---------- spot check and report ----------

def test_wilson_interval():
    assert spot_check.wilson(0, 0) == (0.0, 0.0)
    lo, hi = spot_check.wilson(10, 10)
    assert lo < 1.0 and hi == pytest.approx(1.0)


def test_score_compares_human_labels_to_the_blind_key(tmp_path):
    key = {"s01": "supported", "s02": "supported", "s03": "supported", "s04": "unsupported", "s05": "partial"}
    humans = {"s01": "yes", "s02": "yes", "s03": "partial", "s04": "no", "s05": "yes"}
    kp, cp = tmp_path / "key.json", tmp_path / "labels.csv"
    kp.write_text(__import__("json").dumps({k: {"system_verdict": v} for k, v in key.items()}))
    with cp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, spot_check.FIELDS)
        w.writeheader()
        for k, v in humans.items():
            w.writerow({"id": k, "claim": "c", "source_url": "u", "source_text": "t", "human_verdict": v, "notes": ""})
    s = spot_check.score(cp, kp)
    assert s["precision"] == pytest.approx(2 / 3) and s["lenient_precision"] == pytest.approx(1.0)
    assert s["false_reject_rate"] == pytest.approx(0.5)


def test_report_builds_with_nothing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "RESULTS", tmp_path / "none.json")
    monkeypatch.setattr(report, "ADV", tmp_path / "none2.json")
    monkeypatch.setattr(report, "SPOT", tmp_path / "none3.json")
    monkeypatch.setattr(report, "OUT", tmp_path / "results.md")
    text = report.build().read_text(encoding="utf-8")
    assert "Not run yet" in text and "## 5. Limitations" in text