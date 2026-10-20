"""Shared DuckDuckGo web search (sync — run via asyncio.to_thread).

Queries run in parallel (one DDGS session per query) — sequential queries
cost 0.5-1s each, which stacked on the research critical path.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass
class Source:
    title: str
    url: str
    snippet: str
    query: str

    def to_dict(self, index: int) -> dict[str, str]:
        return {
            "index": index,
            "title": self.title or self.url,
            "url": self.url,
            "snippet": self.snippet,
            "query": self.query,
        }


def _ddgs_class():
    try:
        from ddgs import DDGS

        return DDGS
    except ImportError:  # legacy package name
        from duckduckgo_search import DDGS

        return DDGS


def _search_one(query: str, per_query: int) -> list[dict]:
    """One query in its own DDGS session (thread-safe)."""
    try:
        with _ddgs_class()() as ddgs:
            return list(ddgs.text(query, max_results=per_query))
    except Exception:
        return []


def search_sync(queries: list[str], per_query: int = 4) -> list[Source]:
    """Blocking DuckDuckGo search — call inside a thread executor."""
    queries = [q for q in queries if q and q.strip()]
    if not queries:
        return []
    sources: list[Source] = []
    seen_urls: set[str] = set()
    try:
        with ThreadPoolExecutor(max_workers=min(3, len(queries))) as pool:
            per_query_results = list(pool.map(lambda q: _search_one(q, per_query), queries))
    except Exception:
        return sources
    for query, results in zip(queries, per_query_results, strict=False):
        for r in results:
            url = (r.get("href") or r.get("link") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(
                Source(
                    title=(r.get("title") or "").strip(),
                    url=url,
                    snippet=(r.get("body") or r.get("snippet") or "").strip(),
                    query=query,
                )
            )
    return sources
