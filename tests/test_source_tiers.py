from tools.source_tiers import is_blocked, tier_for


def test_primary_sources():
    assert tier_for("https://www.iea.org/reports/x") == 1.0
    assert tier_for("https://data.gov.uk/dataset") == 1.0
    assert tier_for("https://web.mit.edu/page") == 1.0
    assert tier_for("https://www.ox.ac.uk/news") == 1.0


def test_news_and_blogs():
    assert tier_for("https://www.reuters.com/article") == 0.8
    assert tier_for("https://someone.medium.com/post") == 0.3   # subdomain of medium.com


def test_unknown_and_lookalike_domains_get_default():
    assert tier_for("https://example.com/a") == 0.5
    assert tier_for("https://evilreuters.com/a") == 0.5          # not reuters.com
    assert tier_for("https://fakegov.com/a") == 0.5


def test_blocked():
    assert is_blocked("https://www.youtube.com/watch?v=1")
    assert is_blocked("https://m.facebook.com/x")
    assert not is_blocked("https://www.reuters.com/a")