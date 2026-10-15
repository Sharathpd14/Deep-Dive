import re
from urllib.parse import urlparse

# Tier scores: how much we trust a source type. Visible, editable, testable.
DOMAIN_TIERS: dict[str, float] = {
    # 1.0 primary: institutions, regulators, standards bodies, journals
    "iea.org": 1.0, "worldbank.org": 1.0, "imf.org": 1.0, "oecd.org": 1.0,
    "un.org": 1.0, "europa.eu": 1.0, "bis.org": 1.0, "arxiv.org": 1.0,
    "nature.com": 1.0, "science.org": 1.0, "ieee.org": 1.0,
    # 0.8 major news with editorial standards
    "reuters.com": 0.8, "apnews.com": 0.8, "bloomberg.com": 0.8, "ft.com": 0.8,
    "wsj.com": 0.8, "nytimes.com": 0.8, "bbc.com": 0.8, "bbc.co.uk": 0.8,
    "economist.com": 0.8, "cnbc.com": 0.8, "theguardian.com": 0.8,
    "washingtonpost.com": 0.8,
    # 0.7 analyst and industry research
    "mckinsey.com": 0.7, "bcg.com": 0.7, "deloitte.com": 0.7, "spglobal.com": 0.7,
    "bnef.com": 0.7, "benchmarkminerals.com": 0.7,
    # 0.6 reference and general business press
    "wikipedia.org": 0.6, "britannica.com": 0.6, "investopedia.com": 0.6,
    "forbes.com": 0.6, "techcrunch.com": 0.6,
    # 0.3 user-generated or self-published
    "medium.com": 0.3, "substack.com": 0.3, "blogspot.com": 0.3,
    "wordpress.com": 0.3, "quora.com": 0.3, "reddit.com": 0.3,
    "tumblr.com": 0.3, "wixsite.com": 0.3, "weebly.com": 0.3,
    "technologyreview.com": 0.8, "woodmac.com": 0.7, "morganlewis.com": 0.7,
    "marketsandmarkets.com": 0.6,
}

# Government, university, military, and treaty-body domains, e.g. data.gov.uk, mit.edu, ox.ac.uk
PRIMARY_PATTERN = re.compile(r"\.(gov|edu|ac|mil|int)(\.[a-z]{2})?$")
PRIMARY_TIER = 1.0
DEFAULT_TIER = 0.5

# Sites we never try to fetch (login walls, video, no extractable text)
BLOCKED_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com",
    "x.com", "twitter.com", "pinterest.com", "linkedin.com",
}


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host.removeprefix("www.")


def _walk_up(domain: str):
    """foo.bar.medium.com -> foo.bar.medium.com, bar.medium.com, medium.com, com"""
    labels = domain.split(".")
    for i in range(len(labels)):
        yield ".".join(labels[i:])


def tier_for(url_or_domain: str) -> float:
    domain = domain_of(url_or_domain) if "/" in url_or_domain else url_or_domain.lower()
    if PRIMARY_PATTERN.search(domain):
        return PRIMARY_TIER
    for candidate in _walk_up(domain):
        if candidate in DOMAIN_TIERS:
            return DOMAIN_TIERS[candidate]
    return DEFAULT_TIER


def is_blocked(url_or_domain: str) -> bool:
    domain = domain_of(url_or_domain) if "/" in url_or_domain else url_or_domain.lower()
    return any(c in BLOCKED_DOMAINS for c in _walk_up(domain))