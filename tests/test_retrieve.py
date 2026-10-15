import pytest

from db import connection, store
from tools import retrieve
from tools.fetch import Page
from tools.search import SearchResult

BODY = ("Global electric vehicle battery demand growth continues strongly as "
        "automakers expand production capacity. ") * 6


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    connection.init_db()


@pytest.fixture
def fake_web(monkeypatch):
    results = [
        SearchResult("https://someone.medium.com/p", "Blog", "", "someone.medium.com"),
        SearchResult("https://www.iea.org/r", "IEA", "", "iea.org"),
        SearchResult("https://dead.example.com/x", "Dead", "", "dead.example.com"),
    ]
    monkeypatch.setattr(retrieve, "search", lambda q, max_results=8: results)
    monkeypatch.setattr(
        retrieve, "fetch_page",
        lambda url: None if "dead" in url
        else Page(url, "Title", BODY + f" Unique note from {url}."),
    )


def test_gather_stores_sourced_chunks_and_logs(fake_web):
    run_id = store.create_run("q")
    sq = store.add_sub_question(run_id, "battery demand growth", ["battery demand"])
    out = retrieve.gather(run_id, sq.id, sq.text, sq.queries, max_sources=3)

    assert out["sources"] == 2                        # dead URL failed, others stored
    ev = store.list_evidence(run_id)
    assert {e.domain for e in ev} == {"iea.org", "someone.medium.com"}
    assert {e.tier for e in ev} == {1.0, 0.3}
    actions = [e.action for e in store.list_events(run_id)]
    assert "search" in actions and "fetch_failed" in actions and "source_stored" in actions


def test_ranking_prefers_trusted_source():
    results = [
        SearchResult("https://someone.medium.com/p", "", "", "someone.medium.com"),
        SearchResult("https://www.iea.org/r", "", "", "iea.org"),
    ]
    assert retrieve.rank_candidates(results)[0][0].domain == "iea.org"


def test_second_gather_skips_known_urls(fake_web):
    run_id = store.create_run("q")
    sq = store.add_sub_question(run_id, "battery demand growth", ["battery demand"])
    retrieve.gather(run_id, sq.id, sq.text, sq.queries)
    second = retrieve.gather(run_id, sq.id, sq.text, sq.queries)
    assert second["sources"] == 0                     # nothing new to fetch