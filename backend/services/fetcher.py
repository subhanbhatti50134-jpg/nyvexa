"""Fetch web pages and extract readable text for research/RAG."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

USER_AGENT = "Nyvexa/1.0 (+https://localhost; research assistant)"
MAX_BYTES = 1_500_000
TIMEOUT = 12.0


@dataclass
class Page:
    url: str
    title: str
    text: str


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, text) using a lightweight HTML parse."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Fallback: crude tag strip
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = _clean(re.sub(r"<[^>]+>", "", title_m.group(1))) if title_m else ""
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        return title, _clean(body)[:20000]

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    drop = [
        "script", "style", "noscript", "svg", "iframe",
        "nav", "footer", "header", "aside", "form", "button",
    ]
    for tag in soup(drop):
        tag.decompose()
    # Prefer <article> / <main> when present
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n")
    return title, _clean(text)[:20000]


async def fetch_page(url: str) -> Page | None:
    """Fetch a single URL and extract readable text. Returns None on failure."""
    if not url.startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype and ctype:
                return None
            raw = resp.content[:MAX_BYTES]
            html = raw.decode(resp.charset or "utf-8", errors="replace")
            title, text = extract_text(html)
            if len(text) < 80:
                return None
            return Page(url=str(resp.url), title=title or url, text=text)
    except Exception:
        return None


async def fetch_pages(urls: list[str], limit: int = 3) -> list[Page]:
    """Fetch up to `limit` pages concurrently."""
    import asyncio

    tasks = [fetch_page(u) for u in urls[:limit]]
    results = await asyncio.gather(*tasks)
    return [p for p in results if p is not None]
