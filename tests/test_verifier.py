import pytest

import db.connection as connection
from agents import verifier
from db import store
from models import Chunk, Claim, Verdict

SOURCE = ("According to the IEA, global EV battery demand reached 950 GWh in 2024, "
          "up from 750 GWh in 2023.\nChinese manufacturers supplied most of the cells.")


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


def _chunk(text=SOURCE, cid="E1"):
    return Chunk(id=cid, run_id="r", url="https://www.iea.org/x", domain="iea.org", text=text)


def _claim(text):
    return Claim(id="C1", run_id="r", sub_q_id="Q1", text=text, evidence_ids=["E1"])


# ---------- find_quote ----------

def test_exact_quote_is_found():
    assert verifier.find_quote("demand reached 950 GWh in 2024", [_chunk()]) == "E1"


def test_quote_survives_case_whitespace_and_curly_quotes():
    chunk = _chunk("The \u201cbattery\u201d market   grew\nstrongly in the last year, analysts said.")
    assert verifier.find_quote('The "battery" market grew strongly in the last year', [chunk]) == "E1"


def test_fabricated_quote_is_rejected():
    assert verifier.find_quote("demand reached 1,200 GWh in 2024", [_chunk()]) is None


def test_paraphrased_quote_is_rejected():
    assert verifier.find_quote("EV battery demand hit 950 GWh last year", [_chunk()]) is None


def test_short_quote_is_rejected():
    assert verifier.find_quote("950 GWh", [_chunk()]) is None


def test_ellipsis_parts_must_appear_in_order():
    assert verifier.find_quote("EV battery demand reached ... up from 750 GWh in 2023", [_chunk()]) == "E1"
    assert verifier.find_quote("up from 750 GWh in 2023 ... EV battery demand reached", [_chunk()]) is None


def test_quote_must_be_in_a_cited_chunk():
    other = _chunk("Totally unrelated text about hydrogen fuel cells and trucks.", "E2")
    assert verifier.find_quote("demand reached 950 GWh in 2024", [other]) is None


# ---------- apply_checks ----------

def test_supported_with_fake_quote_becomes_unsupported():
    v = Verdict(verdict="supported", quote="demand reached 1,200 GWh in 2024", reason="ok")
    final, overridden = verifier.apply_checks(_claim("Demand reached 1,200 GWh in 2024."), [_chunk()], v)
    assert final.verdict == "unsupported" and overridden
    assert "quote not found" in final.reason


def test_supported_with_number_missing_from_quote_becomes_partial():
    v = Verdict(verdict="supported", quote="Chinese manufacturers supplied most of the cells", reason="ok")
    final, overridden = verifier.apply_checks(
        _claim("Chinese manufacturers supplied 80 percent of the cells."), [_chunk()], v)
    assert final.verdict == "partial" and overridden


def test_genuinely_supported_claim_passes_untouched():
    v = Verdict(verdict="supported", quote="demand reached 950 GWh in 2024", reason="stated")
    final, overridden = verifier.apply_checks(_claim("Demand reached 950 GWh in 2024."), [_chunk()], v)
    assert final.verdict == "supported" and not overridden


def test_llm_rejections_are_kept_as_they_are():
    v = Verdict(verdict="unsupported", reason="not in text")
    final, overridden = verifier.apply_checks(_claim("Anything of note at all."), [_chunk()], v)
    assert final.verdict == "unsupported" and not overridden


# ---------- the full round ----------

@pytest.fixture
def run_with_claims():
    run_id = store.create_run("q")
    store.add_sub_question(run_id, "How big is battery demand?", ["q"])
    store.add_evidence(run_id, "Q1", "https://www.iea.org/x", "IEA", SOURCE, 1.0)
    good = store.save_claim(run_id, "Q1", "Demand reached 950 GWh in 2024.", ["E1"])
    bad = store.save_claim(run_id, "Q1", "Demand reached 1,200 GWh in 2024.", ["E1"])
    return run_id, good, bad


def test_round_keeps_the_good_claim_and_rejects_the_fake_one(monkeypatch, run_with_claims):
    run_id, good, bad = run_with_claims
    # A lazy model that says "supported" for everything, even with a made-up quote.
    monkeypatch.setattr(verifier, "chat_json", lambda messages, model, **kw: (
        {"verdict": "supported", "quote": "demand reached 950 GWh in 2024", "reason": "ok"}
        if "950" in messages[1]["content"].split("Cited")[0]
        else {"verdict": "supported", "quote": "demand reached 1,200 GWh in 2024", "reason": "ok"}))

    summary = verifier.verify(run_id)
    assert summary["supported"] == 1 and summary["unsupported"] == 1

    verdicts = {c.id: c for c in store.list_claims(run_id)}
    assert verdicts[good.id].verdict == "supported" and verdicts[good.id].quote
    assert verdicts[bad.id].verdict == "unsupported"
    rejected = [e for e in store.list_events(run_id) if e.action == "claim_rejected"]
    assert rejected[0].detail["code_override"] is True


def test_llm_failure_leaves_claim_pending(monkeypatch, run_with_claims):
    run_id, _, _ = run_with_claims

    def boom(*a, **k):
        raise RuntimeError("402 insufficient balance")
    monkeypatch.setattr(verifier, "chat_json", boom)

    assert verifier.verify(run_id)["error"] == 2
    assert all(c.verdict == "pending" for c in store.list_claims(run_id))
    assert "verify_error" in [e.action for e in store.list_events(run_id)]


def test_already_verified_claims_are_not_rechecked(monkeypatch, run_with_claims):
    run_id, good, bad = run_with_claims
    store.set_claim_verdict(run_id, good.id, "supported", "demand reached 950 GWh in 2024", "ok")
    store.set_claim_verdict(run_id, bad.id, "unsupported", "", "no")
    monkeypatch.setattr(verifier, "chat_json", lambda *a, **k: pytest.fail("should not be called"))
    assert verifier.verify(run_id)["supported"] == 0


def test_missing_evidence_is_rejected_without_calling_the_llm(monkeypatch):
    run_id = store.create_run("q")
    store.add_sub_question(run_id, "Anything here at all?", ["q"])
    store.save_claim(run_id, "Q1", "A claim citing evidence that does not exist.", ["E42"])
    monkeypatch.setattr(verifier, "chat_json", lambda *a, **k: pytest.fail("should not be called"))
    assert verifier.verify(run_id)["unsupported"] == 1
    
    
def test_quote_that_covers_only_part_of_the_claim_is_downgraded():
    chunk = _chunk("Sodium-ion is entering limited vehicle and storage deployment, analysts say today.")
    claim = _claim("Sodium-ion batteries are entering limited vehicle deployment, "
                   "but their energy density is lower compared to other battery types.")
    v = Verdict(verdict="supported",
                quote="Sodium-ion is entering limited vehicle and storage deployment", reason="ok")
    final, overridden = verifier.apply_checks(claim, [chunk], v)
    assert final.verdict == "partial" and overridden and "covers only" in final.reason


def test_recheck_supported_downgrades_without_calling_the_llm(monkeypatch, run_with_claims):
    run_id, good, bad = run_with_claims
    store.set_claim_verdict(run_id, good.id, "supported", "demand reached 950 GWh in 2024", "ok")
    store.set_claim_verdict(run_id, bad.id, "supported", "demand reached 950 GWh in 2024", "ok")
    monkeypatch.setattr(verifier, "chat_json", lambda *a, **k: pytest.fail("no LLM calls allowed"))
    assert verifier.recheck_supported(run_id) == 1
    verdicts = {c.id: c.verdict for c in store.list_claims(run_id)}
    assert verdicts[good.id] == "supported" and verdicts[bad.id] == "partial"
    

def test_number_nowhere_in_source_is_still_flagged():
    v = Verdict(verdict="supported", quote="demand reached 950 GWh in 2024", reason="ok")
    final, overridden = verifier.apply_checks(
        _claim("Demand reached 950 GWh in 2024 and will hit 2026 targets."), [_chunk()], v)
    assert final.verdict == "partial" and overridden


def test_year_from_the_question_is_not_held_against_the_claim():
    v = Verdict(verdict="supported", quote="demand reached 950 GWh in 2024", reason="ok")
    claim = _claim("In 2026, demand reached 950 GWh in 2024.")
    final, _ = verifier.apply_checks(claim, [_chunk()], v, allowed_numbers={"2026"})
    assert final.verdict == "supported"
    final, _ = verifier.apply_checks(claim, [_chunk()], v)
    assert final.verdict == "partial"


def test_reset_weak_sends_uncovered_claims_back_to_pending(run_with_claims):
    run_id, good, bad = run_with_claims
    store.set_claim_verdict(run_id, good.id, "supported", "demand reached 950 GWh in 2024", "ok")
    store.set_claim_verdict(run_id, bad.id, "supported", "demand reached 950 GWh in 2024", "ok")
    assert verifier.reset_weak(run_id) == 1
    verdicts = {c.id: c.verdict for c in store.list_claims(run_id)}
    assert verdicts[good.id] == "supported" and verdicts[bad.id] == "pending"