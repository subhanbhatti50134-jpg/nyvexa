"""Async multi-provider LLM service (NVIDIA NIM + OpenRouter, OpenAI-compatible)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import APIError, AsyncOpenAI

from backend.core.config import Settings

NYVEXA_SYSTEM_PROMPT = """\
You are Nyvexa — an intelligent AI assistant with a calm, precise, and curious persona.
You help users think clearly, research deeply, and ship working solutions.

Guidelines:
- Be direct and substantive. Prefer concrete answers over filler.
- Use Markdown: headings, lists, tables, and fenced code blocks when helpful.
- For code, specify the language in the fence and keep snippets runnable.
- When web sources are provided in a research context, ground every claim in them
  and cite inline as [1], [2] matching the numbered source list.
- If sources conflict, say so explicitly and weigh recency/authority.
- Never invent URLs or citations.
"""


def create_client(settings: Settings, provider: str | None = None, model: str | None = None) -> AsyncOpenAI:
    """Create an OpenAI-compatible client. Provider resolved from model id when needed."""
    if provider is None:
        provider = settings.model_provider(model) if model else (
            "nvidia" if settings.nvidia_configured else "openrouter"
        )
    if provider == "nvidia":
        return AsyncOpenAI(base_url=settings.nvidia_base, api_key=settings.nvidia_key)
    return AsyncOpenAI(base_url=settings.api_base, api_key=settings.api_key)


def build_messages(
    history: list[dict[str, str]],
    user_message: str,
    system: str | None = None,
    max_history: int = 24,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system or NYVEXA_SYSTEM_PROMPT}
    ]
    for turn in history[-max_history:]:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def _candidates(settings: Settings, model: str | None) -> list[tuple[str, str]]:
    """Return ordered (provider, model_id) pairs to try."""
    chosen = model or settings.default_model
    chain = [chosen]
    for fb in settings.fallback_chain:
        if fb and fb not in chain:
            chain.append(fb)
    out: list[tuple[str, str]] = []
    for m in chain:
        out.append((settings.model_provider(m), m))
    return out


async def stream_chat(
    client: AsyncOpenAI | None,
    settings: Settings,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """Stream tokens, walking the (provider, model) fallback chain."""
    last_error: Exception | None = None
    for provider, model_id in _candidates(settings, model):
        if not settings.nvidia_configured and provider == "nvidia":
            continue
        if not settings.openrouter_configured and provider == "openrouter":
            continue
        try:
            primary = settings.model_provider(model or settings.default_model)
            cli = client if (client is not None and provider == primary) else None
            if cli is None:
                cli = create_client(settings, provider=provider, model=model_id)
            stream = await cli.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=4096,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
            return
        except APIError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No LLM provider configured")


async def complete_json(
    client: AsyncOpenAI | None,
    settings: Settings,
    messages: list[dict[str, str]],
    model: str | None = None,
) -> Any:
    """Non-streaming completion parsed as JSON (with light repair). Walks fallback chain."""
    last_error: Exception | None = None
    for provider, model_id in _candidates(settings, model)[:3]:
        if not settings.nvidia_configured and provider == "nvidia":
            continue
        if not settings.openrouter_configured and provider == "openrouter":
            continue
        try:
            cli = create_client(settings, provider=provider, model=model_id)
            completion = await cli.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.2,
                max_tokens=1024,
                stream=False,
            )
            raw = completion.choices[0].message.content or "[]"
            return _parse_json_lenient(raw)
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return []


def _parse_json_lenient(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return []
