from dataclasses import dataclass

import httpx
import trafilatura

from config import settings

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DeepDiveBot/0.1; research project)",
    "Accept": "text/html,application/xhtml+xml",
}
MAX_BYTES = 2_000_000
MIN_CHARS = 400          # shorter than this is almost always a stub or paywall


@dataclass
class Page:
    url: str
    title: str
    text: str


def fetch_page(url: str) -> Page | None:
    """Download and extract the main article text. Returns None on any failure."""
    try:
        with httpx.Client(
            follow_redirects=True, timeout=settings.fetch_timeout, headers=HEADERS
        ) as client:
            r = client.get(url)
    except httpx.HTTPError:
        return None

    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or ("html" not in ctype and "xml" not in ctype):
        return None
    if len(r.content) > MAX_BYTES:
        return None

    html = r.text
    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_precision=True
    )
    if not text or len(text) < MIN_CHARS:
        return None

    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "") or ""
    return Page(url=url, title=title, text=text)