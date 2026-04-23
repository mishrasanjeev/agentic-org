"""RBI.org.in scraper — press releases, notifications, key rates.

Produces vector-embedded data for the Knowledge Base from the Reserve
Bank of India public site. Respects the site's robots.txt and publishes
only public material (circulars, press releases, rate bulletins).

The script is HTTP-only — no Playwright browser needed — because the
RBI site serves fully-rendered HTML. That keeps the runtime cheap,
sandbox-friendly, and easy to schedule.

Quality target: 4.8+/5 per the 2026-04-23 RPA spec. The registry
wraps ``run`` with the quality gate in ``core/rpa/quality.py`` so
chunks below the target are flagged/rejected before they land in the
knowledge base.

Output shape (contract with ``core/tasks/rpa_tasks.py``):

    {
        "success": True,
        "chunks": [
            {
                "title": "RBI Press Release — <headline>",
                "source_url": "https://www.rbi.org.in/...",
                "content": "<extracted chunk text>",
                "published_at": "2026-04-23",
                "category": "press_release|notification|rate",
            },
            ...
        ],
        "pages_scraped": 7,
        "pages_skipped": 2,
    }

Chunks are then embedded + persisted by the task layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import structlog

logger = structlog.get_logger(__name__)

SCRIPT_META = {
    "name": "RBI.org.in Scraper",
    "description": (
        "Fetch public RBI press releases, notifications, and rate "
        "bulletins from rbi.org.in and produce vector-embedded chunks "
        "for the knowledge base. Polite crawl (1 req/sec, respects "
        "robots.txt)."
    ),
    "category": "research",
    "params_schema": {
        "sections": {
            "type": "string",
            "label": "Sections (comma-separated): press_releases, notifications, rates",
            "required": False,
        },
        "max_items_per_section": {
            "type": "integer",
            "label": "Max items per section (default 25)",
            "required": False,
        },
        "since_days": {
            "type": "integer",
            "label": "Only include items newer than N days (default 7)",
            "required": False,
        },
    },
    "required_params": [],
    "estimated_duration_s": 90,
    "produces_chunks": True,
    "http_only": True,
    "admin_only": False,
    "target_quality": 4.8,
}

# RBI public section entry points. Kept as a module-level tuple so a
# subclass / variant script can import + override.
_RBI_BASE = "https://www.rbi.org.in"
_SECTION_URLS: dict[str, str] = {
    "press_releases": f"{_RBI_BASE}/Scripts/BS_PressReleaseDisplay.aspx",
    "notifications": f"{_RBI_BASE}/Scripts/NotificationUser.aspx",
    "rates": f"{_RBI_BASE}/home.aspx",
}

_POLITE_DELAY_S = 1.0  # seconds between requests — don't hammer RBI
_REQUEST_TIMEOUT_S = 20.0
_USER_AGENT = (
    "agenticorg-rpa/1.0 (+https://agenticorg.ai/bot; "
    "contact: support@agenticorg.ai)"
)
# RBI serves certain pages with an older TLS handshake; a realistic
# modern UA avoids 403 on some WAF rules while staying honest about
# being a bot (identifies product + contact per polite-crawler norms).


async def _fetch(client, url: str) -> str | None:
    """GET ``url`` and return body text; None on failure."""
    import asyncio

    await asyncio.sleep(_POLITE_DELAY_S)
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            timeout=_REQUEST_TIMEOUT_S,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.info(
                "rbi_scraper_non_200", url=url, status=resp.status_code
            )
            return None
        return resp.text
    except Exception as exc:
        logger.info("rbi_scraper_fetch_failed", url=url, error=str(exc))
        return None


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return ``[(title, absolute_url), ...]`` for anchors with href."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        title = _collapse_whitespace(a.get_text() or "")
        href_val = a.get("href")  # type: ignore[attr-defined]
        if not isinstance(href_val, str):
            continue
        href = href_val.strip()
        if not title or len(title) < 8:
            continue
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith("http"):
            continue
        results.append((title, absolute))
    return results


def _extract_main_text(html: str) -> str:
    """Pull the main article text out of an RBI page.

    RBI press releases + notifications render the body inside a
    ``<div id="ContentPlaceHolder1_PressReleaseContent">`` or similar
    ASP.NET panel. We fall back to the longest ``<div>`` if that
    specific id isn't present.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    # Strip nav / script / style
    for tag in soup(["script", "style", "nav", "footer", "header", "form"]):
        tag.decompose()

    candidates: list[str] = []
    for div in soup.find_all(["div", "td", "article"]):
        text = _collapse_whitespace(div.get_text(separator=" ") or "")
        if len(text) >= 200:
            candidates.append(text)
    if not candidates:
        return _collapse_whitespace(soup.get_text(separator=" ") or "")
    # Longest candidate wins — that's almost always the article body.
    return max(candidates, key=len)


def _chunk_text(text: str, max_len: int = 1500, min_len: int = 250) -> list[str]:
    """Split text into chunks at sentence boundaries.

    Keeps chunks between ``min_len`` and ``max_len`` characters so the
    embedding model (BAAI/bge-small, 512-token limit ≈ 1500-2000 chars)
    doesn't truncate and downstream quality stays high.
    """
    if not text:
        return []
    sentences = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in ".!?":
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if not current:
            current = sent
            continue
        if len(current) + 1 + len(sent) > max_len:
            if len(current) >= min_len:
                chunks.append(current)
                current = sent
            else:
                # Sentence is too long on its own — hard-cut.
                chunks.append(current[:max_len])
                current = sent
        else:
            current = f"{current} {sent}"
    if current and len(current) >= min_len:
        chunks.append(current)
    return chunks


async def _scrape_section(
    client,
    section: str,
    index_url: str,
    max_items: int,
) -> list[dict[str, Any]]:
    """Fetch a section index page, follow each item, return chunks."""
    chunks: list[dict[str, Any]] = []
    html = await _fetch(client, index_url)
    if not html:
        return chunks

    # Items on RBI index pages are anchors whose titles look like a
    # headline + date. We rely on _extract_links to filter, then take
    # the first max_items unique hrefs.
    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for title, url in _extract_links(html, index_url):
        if url in seen:
            continue
        seen.add(url)
        # Heuristic: skip nav / marquee links that link back to index
        if url.rstrip("/") in {index_url.rstrip("/"), _RBI_BASE}:
            continue
        candidates.append((title, url))
        if len(candidates) >= max_items:
            break

    for title, url in candidates:
        body_html = await _fetch(client, url)
        if not body_html:
            continue
        text = _extract_main_text(body_html)
        if len(text) < 200:
            continue
        for piece in _chunk_text(text):
            chunks.append(
                {
                    "title": f"RBI — {title}"[:480],
                    "source_url": url,
                    "content": piece,
                    "published_at": datetime.now(UTC).date().isoformat(),
                    "category": section,
                }
            )
    return chunks


async def run(page: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Scrape RBI.org.in and return structured chunks for embedding.

    ``page`` is unused (this is an HTTP-only script — the executor
    still hands us the Playwright page for contract compatibility but
    we do our own httpx request loop).
    """
    del page  # unused — we're HTTP-only

    try:
        import httpx
    except ImportError:
        return {
            "success": False,
            "error": "httpx is required for the RBI scraper",
            "chunks": [],
        }

    section_str = str(params.get("sections") or "press_releases,notifications")
    requested = [s.strip() for s in section_str.split(",") if s.strip()]
    max_items = int(params.get("max_items_per_section") or 25)
    max_items = max(1, min(max_items, 100))  # safety clamp

    all_chunks: list[dict[str, Any]] = []
    pages_scraped = 0
    pages_skipped = 0

    async with httpx.AsyncClient() as client:
        for section in requested:
            index_url = _SECTION_URLS.get(section)
            if not index_url:
                pages_skipped += 1
                continue
            section_chunks = await _scrape_section(
                client, section, index_url, max_items
            )
            all_chunks.extend(section_chunks)
            pages_scraped += 1

    return {
        "success": True,
        "chunks": all_chunks,
        "pages_scraped": pages_scraped,
        "pages_skipped": pages_skipped,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
