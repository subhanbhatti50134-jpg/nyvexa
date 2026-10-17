"""Research engine: chat-only, standard research, and deep multi-round research.

All modes share the SSE emit protocol:
  research {step, message, ...} | sources {sources} | token {text} |
  usage {prompt_tokens, completion_tokens, total_tokens, model} | done {mode, ...}
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from backend.core.config import Settings
from backend.services.fetcher import fetch_pages
from backend.services.llm import build_messages, complete_json
from backend.services.websearch import Source, search_sync

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

PLANNER_PROMPT = """\
You are a research planner. Given a user question, produce 2-{max_queries} concise web
search queries that together would fully answer it. Output ONLY a JSON array of strings.
No prose, no markdown fence.

Question: {question}
"""

SYNTHESIS_SYSTEM = """\
You are Nyvexa in autonomous research mode. You have gathered live web excerpts.
Write a thorough, well-structured Markdown answer grounded STRICTLY in the sources below.

Rules:
- Cite claims inline with [1], [2] style markers that match the source numbers.
- Open with a 1-2 sentence direct answer, then supporting detail.
- Note conflicts or uncertainty between sources when they exist.
- End with a short "Sources" listing using Markdown links: [n] [Title](url).
- Do not fabricate facts, quotes, or URLs beyond the provided list.
"""


def _format_sources_block(sources: list[Source], pages_text: str = "") -> str:
    lines = []
    for i, s in enumerate(sources, start=1):
        lines.append(f"[{i}] {s.title}\nURL: {s.url}\nExcerpt: {s.snippet[:800]}")
    block = "\n\n".join(lines)
    if pages_text:
        block += f"\n\n---\nFull page extracts:\n\n{pages_text}"
    return block


async def plan_queries(
    client: AsyncOpenAI, settings: Settings, question: str
) -> list[str]:
    prompt = PLANNER_PROMPT.format(
        max_queries=settings.research_max_queries, question=question
    )
    data = await complete_json(
        client, settings, [{"role": "user", "content": prompt}], model=settings.default_model
    )
    queries: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.strip():
                queries.append(item.strip()[:200])
    return queries[: settings.research_max_queries] or [question[:200]]


async def _gather_sources(
    client: AsyncOpenAI | None,
    settings: Settings,
    question: str,
    emit: Emit,
    fetch_full: bool,
) -> tuple[list[Source], str]:
    """Plan → search → (optionally) fetch top pages. Returns (sources, pages_text)."""
    await emit("research", {"step": "planning", "message": "Planning research queries…"})
    if client is not None:
        try:
            queries = await plan_queries(client, settings, question)
        except Exception:
            queries = [question[:200]]
    else:
        queries = [question[:200]]

    await emit(
        "research",
        {
            "step": "searching",
            "message": f"Searching the web ({len(queries)} queries)…",
            "queries": queries,
        },
    )
    sources = await asyncio.to_thread(
        search_sync, queries, settings.research_results_per_query
    )
    await emit("sources", {"sources": [s.to_dict(i) for i, s in enumerate(sources, start=1)]})

    pages_text = ""
    if fetch_full and sources:
        await emit(
            "research",
            {"step": "fetching", "message": f"Reading top {min(3, len(sources))} pages in full…"},
        )
        pages = await fetch_pages([s.url for s in sources], limit=3)
        chunks = []
        for i, page in enumerate(pages, start=1):
            # find source index by URL
            idx = next(
                (j for j, s in enumerate(sources, 1) if s.url == page.url or page.url.startswith(s.url)),
                i,
            )
            chunks.append(f"--- Page [{idx}] {page.title} ({page.url}) ---\n{page.text[:5000]}")
        pages_text = "\n\n".join(chunks)

    return sources, pages_text


async def stream_research(
    client: AsyncOpenAI,
    settings: Settings,
    question: str,
    history: list[dict[str, str]],
    model: str | None,
    emit: Emit,
    deep: bool = False,
) -> AsyncIterator[str]:
    """Standard research (deep=False) or deep multi-round research (deep=True)."""
    rounds = 1
    if deep:
        try:
            rounds = max(2, int(os.getenv("NYVEXA_DEEP_ROUNDS", "3")))
        except ValueError:
            rounds = 3

    all_sources: list[Source] = []
    seen_urls: set[str] = set()
    pages_text = ""

    for round_num in range(1, rounds + 1):
        if deep:
            await emit(
                "research",
                {"step": "round", "message": f"Research round {round_num}/{rounds}"},
            )
            if round_num > 1:
                # Ask the model what's still missing based on sources so far
                context = _format_sources_block(all_sources, pages_text)
                try:
                    followups = await complete_json(
                        client,
                        settings,
                        [
                            {
                                "role": "user",
                                "content": (
                                    "You already researched this question using these sources:\n\n"
                                    f"{context}\n\nQuestion: {question}\n\n"
                                    "List 1-2 NEW search queries that would fill remaining gaps. "
                                    "Output ONLY a JSON array of strings."
                                ),
                            }
                        ],
                        model=settings.default_model,
                    )
                    extra_queries = [
                        q.strip()[:200] for q in followups if isinstance(q, str) and q.strip()
                    ][:2]
                except Exception:
                    extra_queries = []
                if extra_queries:
                    await emit(
                        "research",
                        {
                            "step": "searching",
                            "message": f"Round {round_num}: follow-up searches…",
                            "queries": extra_queries,
                        },
                    )
                    more = await asyncio.to_thread(
                        search_sync, extra_queries, settings.research_results_per_query
                    )
                    for s in more:
                        if s.url not in seen_urls:
                            seen_urls.add(s.url)
                            all_sources.append(s)
                    await emit(
                        "sources",
                        {"sources": [s.to_dict(i) for i, s in enumerate(all_sources, start=1)]},
                    )

        sources, pages_round = await _gather_sources(
            client, settings, question, emit, fetch_full=True
        )
        for s in sources:
            if s.url not in seen_urls:
                seen_urls.add(s.url)
                all_sources.append(s)
        if pages_round:
            pages_text = (pages_text + "\n\n" + pages_round)[:24000]
        await emit(
            "sources",
            {"sources": [s.to_dict(i) for i, s in enumerate(all_sources, start=1)]},
        )

    if not all_sources:
        await emit(
            "research",
            {"step": "fallback", "message": "No live results — answering from model knowledge…"},
        )

    context = _format_sources_block(all_sources, pages_text) if all_sources else "(No web sources retrieved.)"
    messages = build_messages(
        history,
        f"Research question:\n{question}\n\n---\nWeb sources gathered:\n\n{context}",
        system=SYNTHESIS_SYSTEM,
        max_history=settings.max_history,
    )

    await emit("research", {"step": "synthesizing", "message": "Synthesizing findings…"})

    usage_out: dict[str, Any] = {}
    async for item in _stream_with_usage(client, settings, messages, model or settings.default_model):
        kind, payload = item
        if kind == "token":
            yield payload
        elif kind == "usage":
            usage_out = payload

    await emit(
        "done",
        {
            "mode": "deep" if deep else "research",
            "source_count": len(all_sources),
            "usage": usage_out,
        },
    )


async def _stream_with_usage(
    client: AsyncOpenAI | None, settings: Settings, messages: list[dict[str, str]], model: str
) -> AsyncIterator[tuple[str, Any]]:
    """Yield ('token', text) and finally ('usage', usage_dict). Multi-provider fallback."""
    from openai import APIError

    from backend.services.llm import _candidates, create_client

    usage: dict[str, Any] = {}
    stream = None
    last_error: Exception | None = None
    actual_model = model

    for provider, candidate in _candidates(settings, model):
        if not settings.nvidia_configured and provider == "nvidia":
            continue
        if not settings.openrouter_configured and provider == "openrouter":
            continue
        for with_usage_flag in (True, False):
            try:
                cli = create_client(settings, provider=provider, model=candidate)
                kwargs: dict[str, Any] = {
                    "model": candidate,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 4096,
                    "stream": True,
                }
                if with_usage_flag:
                    kwargs["stream_options"] = {"include_usage": True}
                stream = await cli.chat.completions.create(**kwargs)
                actual_model = candidate
                break
            except APIError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue
        if stream is not None:
            break
    if stream is None:
        raise last_error or RuntimeError("All models failed")

    async for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
                "model": actual_model,
            }
        if chunk.choices:
            delta = chunk.choices[0].delta.content if chunk.choices[0].delta else None
            if delta:
                yield ("token", delta)
    if usage:
        yield ("usage", usage)


async def run_chat_only(
    client: AsyncOpenAI,
    settings: Settings,
    question: str,
    history: list[dict[str, str]],
    model: str | None,
    emit: Emit,
    rag_context: str = "",
) -> AsyncIterator[str]:
    """Plain streaming chat with optional RAG context injected."""
    await emit("research", {"step": "chat", "message": "Thinking…"})
    prompt = question
    if rag_context:
        prompt = (
            "Use the following knowledge-base excerpts when relevant (cite as [KB]):\n\n"
            f"{rag_context}\n\n---\nUser question: {question}"
        )
    messages = build_messages(history, prompt, max_history=settings.max_history)

    usage: dict[str, Any] = {}
    async for item in _stream_with_usage(
        client, settings, messages, model or settings.default_model
    ):
        kind, payload = item
        if kind == "token":
            yield payload
        elif kind == "usage":
            usage = payload

    await emit("done", {"mode": "chat", "usage": usage})
