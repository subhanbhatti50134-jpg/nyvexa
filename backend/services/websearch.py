"""Shared DuckDuckGo web search (sync — run via asyncio.to_thread)."""

from __future__ import annotations

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


def search_sync(queries: list[str], per_query: int = 4) -> list[Source]:
    """Blocking DuckDuckGo search — call inside a thread executor."""
    sources: list[Source] = []
    seen_urls: set[str] = set()
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # legacy package name

    try:
        with DDGS() as ddgs:
            for query in queries:
                try:
                    results = list(ddgs.text(query, max_results=per_query))
                except Exception:
                    continue
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
    except Exception:
        return sources
    return sources
