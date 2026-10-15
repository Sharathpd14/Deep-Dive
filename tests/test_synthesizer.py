import pytest

from agents import synthesizer
from coverage import thin_subquestions
from db import connection, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


@pytest.fixture
def run_id():
    rid = store.create_run("What drives battery demand?")
    store.add_sub_question(rid, "How big is battery demand?", ["q"])
    store.add_sub_question(rid, "Who are the key players?", ["q"])
    store.add_evidence(rid, "Q1", "https://www.iea.org/x", "IEA Outlook", "Demand reached 950 GWh in 2024. " * 3, 1.0)
    store.add_evidence(rid, "Q1", "https://www.reuters.com/y", "Reuters", "Battery prices fell 20 percent in 2024. " * 3, 0.8)
    c1 = store.save_claim(rid, "Q1", "Demand reached 950 GWh in 2024.", ["E1"])
    c2 = store.save_claim(rid, "Q1", "Battery prices fell 20 percent in 2024.", ["E2"])
    c3 = store.save_claim(rid, "Q1", "Cells came mostly from Asia.", ["E1"])
    store.set_claim_verdict(rid, c1.id, "supported", "Demand reached 950 GWh in 2024.", "stated")
    store.set_claim_verdict(rid, c2.id, "supported", "Battery prices fell 20 percent in 2024.", "stated")
    store.set_claim_verdict(rid, c3.id, "partial", "Cells came mostly from Asia", "source says China only")
    return rid


GOOD = {"paragraphs": ["Demand reached 950 GWh in 2024 [C1]. Prices fell 20 percent [C2]."], "conflicts": []}


def _fake(output):
    calls = []

    def fn(messages, model, **kw):
        calls.append(messages)
        if isinstance(output, Exception):
            raise output
        return output
    fn.calls = calls
    return fn


def test_check_paragraph_rules(run_id):
    claims = {c.id: c for c in store.list_claims(run_id, verdict="supported")}
    allowed = set(claims)
    assert synthesizer.check_paragraph("Demand reached 950 GWh [C1].", allowed, claims, set()) is None
    assert "no citation" in synthesizer.check_paragraph("Demand grew.", allowed, claims, set())
    assert "not provided" in synthesizer.check_paragraph("Demand grew [C9].", allowed, claims, set())
    assert "numbers" in synthesizer.check_paragraph("Demand reached 1200 GWh [C1].", allowed, claims, set())
    assert synthesizer.check_paragraph("In 2026 demand reached 950 GWh [C1].", allowed, claims, {"2026"}) is None


def test_report_cites_real_urls_and_lists_sources(monkeypatch, run_id):
    fake = _fake(GOOD)
    monkeypatch.setattr(synthesizer, "chat_json", fake)
    report = synthesizer.synthesize(run_id)

    assert "[[1]](https://www.iea.org/x)" in report
    assert "[[2]](https://www.reuters.com/y)" in report
    assert "1. IEA Outlook — iea.org (trust 1.0)" in report
    assert "[C1]" not in report.split("## Appendix A")[0]       # all tokens were converted
    assert len(fake.calls) == 1                                  # Q2 has no claims: no LLM call
    assert store.get_run(run_id)["report_md"] == report


def test_only_supported_claims_appear_in_the_body(monkeypatch, run_id):
    monkeypatch.setattr(synthesizer, "chat_json", _fake(GOOD))
    report = synthesizer.synthesize(run_id)
    body, appendix_b = report.split("## Appendix B")
    assert "Cells came mostly from Asia" not in body
    assert "Cells came mostly from Asia. — *partial*: source says China only" in appendix_b
    assert "2 of 3 drafted claims passed verification" in report


def test_fabricated_number_is_dropped_and_falls_back_to_verified_claims(monkeypatch, run_id):
    monkeypatch.setattr(synthesizer, "chat_json",
                        _fake({"paragraphs": ["Demand reached 1200 GWh [C1]."], "conflicts": []}))
    report = synthesizer.synthesize(run_id)
    assert "1200" not in report
    assert "- Demand reached 950 GWh in 2024." in report         # fallback bullet
    assert "paragraph_dropped" in [e.action for e in store.list_events(run_id)]


def test_unknown_claim_id_is_dropped(monkeypatch, run_id):
    monkeypatch.setattr(synthesizer, "chat_json",
                        _fake({"paragraphs": ["Demand is strong [C9]."], "conflicts": []}))
    report = synthesizer.synthesize(run_id)
    assert "Demand is strong" not in report


def test_llm_failure_still_produces_a_report(monkeypatch, run_id):
    monkeypatch.setattr(synthesizer, "chat_json", _fake(RuntimeError("402")))
    report = synthesizer.synthesize(run_id)
    assert "- Demand reached 950 GWh in 2024." in report
    assert "synthesis_failed" in [e.action for e in store.list_events(run_id)]


def test_thin_subquestion_is_listed_not_padded(monkeypatch, run_id):
    monkeypatch.setattr(synthesizer, "chat_json", _fake(GOOD))
    report = synthesizer.synthesize(run_id)
    assert "## Areas with insufficient evidence" in report
    assert "Who are the key players?" in report.split("## Areas with insufficient evidence")[1]
    assert "## Who are the key players?" not in report            # no section for Q2


def test_conflicts_are_validated_and_shown(monkeypatch, run_id):
    out = {"paragraphs": ["Demand reached 950 GWh in 2024 [C1]."],
           "conflicts": [{"claims": ["C1", "C2"], "note": "These differ."},
                         {"claims": ["C1", "C99"], "note": "Invented pair."}]}
    monkeypatch.setattr(synthesizer, "chat_json", _fake(out))
    report = synthesizer.synthesize(run_id)
    assert "## Where sources disagree" in report and "These differ." in report
    assert "Invented pair." not in report


def test_appendix_shows_supporting_quotes(monkeypatch, run_id):
    monkeypatch.setattr(synthesizer, "chat_json", _fake(GOOD))
    appendix = synthesizer.synthesize(run_id).split("## Appendix A")[1]
    assert '> "Demand reached 950 GWh in 2024."' in appendix


def test_coverage_flags_only_the_thin_subquestion(run_id):
    thin = thin_subquestions(run_id)
    assert list(thin) == ["Q2"] and thin["Q2"] == {"claims": 0, "domains": 0}
    

def test_adjacent_citations_to_the_same_source_are_merged(run_id):
    claims = {c.id: c for c in store.list_claims(run_id, verdict="supported")}
    src = synthesizer.Sources(run_id)
    assert src.cite("Fact [C1][C1].", claims) == "Fact [[1]](https://www.iea.org/x)."
    assert src.cite("Fact [C1][C2].", claims) == (
        "Fact [[1]](https://www.iea.org/x)[[2]](https://www.reuters.com/y).")
    

def _supported(run_id):
    return {c.id: c for c in store.list_claims(run_id, verdict="supported")}


def test_faithful_sentence_is_kept(run_id):
    text = "Prices fell 20 percent in 2024 [C2]."
    assert synthesizer.ground_sentences(text, _supported(run_id)) == (text, 0)


def test_interpretive_clause_is_replaced_by_the_verified_claim(run_id):
    text = "Demand reached 950 GWh in 2024, which signals a booming market with strong investor appetite [C1]."
    out, changed = synthesizer.ground_sentences(text, _supported(run_id))
    assert (out, changed) == ("Demand reached 950 GWh in 2024 [C1].", 1)


def test_uncited_trailing_sentence_is_dropped(run_id):
    text = "Demand reached 950 GWh in 2024 [C1]. And that is great news."
    out, changed = synthesizer.ground_sentences(text, _supported(run_id))
    assert out == "Demand reached 950 GWh in 2024 [C1]." and changed == 1