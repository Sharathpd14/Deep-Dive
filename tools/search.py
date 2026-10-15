from dataclasses import dataclass
from urllib.parse import urldefrag

from ddgs import DDGS
from tenacity import retry, stop_after_attempt, wait_exponential

from tools.source_tiers import domain_of, is_blocked


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    domain: str


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def _raw_search(query: str, n: int) -> list[dict]:
    return DDGS().text(query, max_results=n) or []


def search(query: str, max_results: int = 8) -> list[SearchResult]:
    """Raises on persistent failure so the caller can log it to the trail."""
    raw = _raw_search(query, max_results * 2)      # over-fetch, some get filtered
    out, seen = [], set()
    for r in raw:
        url = urldefrag(r.get("href", ""))[0].rstrip("/")
        if not url.startswith("http") or url in seen or is_blocked(url):
            continue
        seen.add(url)
        out.append(SearchResult(url, r.get("title", ""), r.get("body", ""), domain_of(url)))
        if len(out) >= max_results:
            break
    return out