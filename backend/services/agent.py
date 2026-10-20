"""OpenRouter tool-calling agent loop.

The model can call web_search, fetch_page, calculator, run_python, rag_search
before producing its final streamed answer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from backend.core.config import Settings
from backend.services.llm import build_messages
from backend.services.tools import MAX_TOOL_ROUNDS, TOOL_SCHEMAS, execute_tool

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

AGENT_SYSTEM = """\
You are Nyvexa — an intelligent AI assistant with autonomous tool use.
You have tools for web search, fetching pages, arithmetic, Python execution,
and searching the user's uploaded documents.

Guidelines:
- Use tools when they would improve accuracy (facts, current events, math, code).
- Prefer one well-chosen tool call over many scattered ones.
- Cite web sources inline as [1], [2] when you used web_search/fetch_page;
  list sources at the end as Markdown links.
- When using rag_search excerpts, mark them as [KB].
- After tools return, synthesize a clear Markdown answer. Do not mention the
  tool plumbing unless asked.
- If tools fail, answer from your own knowledge and say so briefly.
"""


async def stream_agent(
    client: AsyncOpenAI,
    settings: Settings,
    question: str,
    history: list[dict[str, str]],
    model: str | None,
    emit: Emit,
) -> AsyncIterator[str]:
    """Run the multi-turn tool loop, streaming the final answer."""
    chosen = model or settings.default_model
    messages = build_messages(
        history, question, system=AGENT_SYSTEM, max_history=settings.max_history,
        history_char_budget=getattr(settings, "history_char_budget", None),
    )

    collected_sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    usage: dict[str, Any] = {}

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        await emit(
            "research",
            {"step": "thinking", "message": f"Reasoning (round {round_num})…"},
        )

        # Walk fallback chain for the non-streaming tool-calling call
        response = None
        last_err = None
        from openai import APIError

        from backend.services.llm import _candidates, create_client

        for provider, candidate in _candidates(settings, chosen):
            if not settings.nvidia_configured and provider == "nvidia":
                continue
            if not settings.openrouter_configured and provider == "openrouter":
                continue
            try:
                cli = create_client(settings, provider=provider, model=candidate)
                response = await cli.chat.completions.create(
                    model=candidate,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    temperature=0.4,
                    max_tokens=2048,
                    stream=False,
                )
                chosen = candidate
                break
            except APIError as exc:
                last_err = exc
                continue
            except Exception as exc:
                last_err = exc
                continue
        if response is None:
            raise last_err or RuntimeError("All models failed")
        choice = response.choices[0]
        msg = choice.message
        tool_calls = msg.tool_calls or []

        # Record assistant message (with tool_calls) for the conversation
        assistant_entry: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        messages.append(assistant_entry)

        if not tool_calls:
            # Final answer — stream it for UX (re-stream the text)
            final_text = msg.content or ""
            if not final_text and getattr(msg, "reasoning", None):
                # Reasoning-only model: ask for a plain final answer without tools
                messages.append({"role": "assistant", "content": msg.reasoning[:2000]})
                messages.append(
                    {"role": "user", "content": "Give your final answer now, concisely, without tools."}
                )
                from backend.services.research import _stream_with_usage

                async for item in _stream_with_usage(client, settings, messages, chosen):
                    kind, payload = item
                    if kind == "token":
                        yield payload
                    elif kind == "usage":
                        usage = payload
                if collected_sources:
                    await emit("sources", {"sources": collected_sources})
                await emit("done", {"mode": "agent", "usage": usage})
                return
            if final_text:
                # stream in chunks for UX feel
                step = max(12, len(final_text) // 60)
                for i in range(0, len(final_text), step):
                    yield final_text[i : i + step]
            if getattr(response, "usage", None):
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "model": chosen,
                }
            if collected_sources:
                await emit("sources", {"sources": collected_sources})
            await emit("done", {"mode": "agent", "usage": usage})
            return

        # Execute each tool call, append tool results
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            await emit(
                "research",
                {
                    "step": "tool",
                    "message": f"Using tool: {name}",
                    "tool": name,
                    "args": {k: str(v)[:120] for k, v in args.items()},
                },
            )

            result_json = await execute_tool(name, args)

            # Track web sources for the UI panel
            if name == "web_search":
                try:
                    results = json.loads(result_json).get("results", [])
                    for r in results:
                        url = r.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            collected_sources.append(
                                {
                                    "index": len(collected_sources) + 1,
                                    "title": r.get("title") or url,
                                    "url": url,
                                    "snippet": r.get("snippet", ""),
                                    "query": args.get("query", ""),
                                }
                            )
                    await emit("sources", {"sources": collected_sources})
                except json.JSONDecodeError:
                    pass

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json[:8000],
                }
            )

    # Ran out of rounds — force a final answer without tools
    await emit("research", {"step": "synthesizing", "message": "Finalizing answer…"})
    messages.append(
        {
            "role": "user",
            "content": "Tool budget exhausted. Give your best final answer now using what you learned.",
        }
    )
    from backend.services.research import (
        _stream_with_usage,  # late import to avoid cycles
    )

    async for item in _stream_with_usage(client, settings, messages, chosen):
        kind, payload = item
        if kind == "token":
            yield payload
        elif kind == "usage":
            usage = payload

    if collected_sources:
        await emit("sources", {"sources": collected_sources})
    await emit("done", {"mode": "agent", "usage": usage})
