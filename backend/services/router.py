"""Auto mode — instant keyword heuristic with an optional bounded LLM tie-break.

The heuristic is free (0ms). An LLM classifier was measured at 3-10s per call
on the free/reasoning model pool (nemotron reasoning burns 500-900 hidden
tokens before answering; :free models queue), so the LLM is only consulted
when the keywords are genuinely ambiguous (multiple categories matched) and
only with a hard wall-clock budget.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from openai import AsyncOpenAI

from backend.core.config import Settings
from backend.services.llm import complete_json

ROUTER_PROMPT = """\
You are a routing classifier for an AI assistant. Given the user's message, pick the
best execution mode. Respond with ONLY a JSON object, no prose.

Modes:
- chat: normal conversation, explanations, code writing, opinions, math you can do mentally.
- research: needs current/live web facts (news, prices, dates, versions, "latest", "today").
- deep: broad research needing many angles over a longer time budget.
- agent: must run code, compute precisely, fetch a specific URL, or search the user's docs.

Rules: prefer chat unless the message clearly needs live data or tool execution.
When unsure between chat and research, choose chat.

{"mode": "...", "reason": "<max 8 words>"}

Message: __QUESTION__
"""

_KW_TIME = re.compile(
    r"\b(today|latest|current|now|recent|news|price|weather|stock|score|release|version|202\d)\b",
    re.I,
)
_KW_DEEP = re.compile(r"\b(research|report|analysis|investigate|deep dive|compare .+ options)\b", re.I)
# Tightened: bare "run" caught "run a marathon"/"how to run Docker" and sent
# them through the slow agent loop. Require tool-shaped targets.
_KW_AGENT = re.compile(
    r"\b(?:run|execute)\s+(?:python|py\b|a\s+script|the\s+script|code|this\s+code)"
    r"|\bcalculate\b|\bcompute\b|\bscrape\b"
    r"|fetch\s+https?://|\bpython\s+script\b",
    re.I,
)

# Deterministic priority when keywords collide (agent evidence is strongest).
_FALLBACK_ORDER = ("agent", "deep", "research")


def _heuristic_hits(question: str) -> set[str]:
    hits: set[str] = set()
    if _KW_AGENT.search(question):
        hits.add("agent")
    if _KW_TIME.search(question):
        hits.add("research")
    if _KW_DEEP.search(question):
        hits.add("deep")
    return hits


def _heuristic(question: str) -> str:
    hits = _heuristic_hits(question)
    for mode in _FALLBACK_ORDER:
        if mode in hits:
            return mode
    return "chat"


async def route_mode(
    client: AsyncOpenAI | None, settings: Settings, question: str
) -> tuple[str, str]:
    """Return (mode, reason). Instant heuristic; LLM only on ambiguity, hard-bounded."""
    hits = _heuristic_hits(question)
    if not hits:
        return "chat", "heuristic"
    if len(hits) == 1:
        return next(iter(hits)), "keyword heuristic"

    # Ambiguous: multiple keyword categories matched — spend the LLM budget here.
    fallback = _heuristic(question)
    try:
        data: Any = await asyncio.wait_for(
            complete_json(
                client,
                settings,
                [
                    {
                        "role": "user",
                        "content": ROUTER_PROMPT.replace("__QUESTION__", question[:400]),
                    }
                ],
                max_tokens=160,
                timeout=settings.router_timeout,
            ),
            timeout=settings.router_timeout + 1.0,
        )
        if isinstance(data, dict):
            mode = str(data.get("mode", "")).strip().lower()
            reason = str(data.get("reason", "")).strip()[:80] or "auto"
            if mode in {"chat", "research", "deep", "agent"}:
                return mode, reason
        if isinstance(data, str):  # light repair: model echoed bare JSON string
            parsed = json.loads(data)
            if isinstance(parsed, dict) and parsed.get("mode") in {"chat", "research", "deep", "agent"}:
                return str(parsed["mode"]).lower(), str(parsed.get("reason", "auto"))
    except Exception:
        pass
    return fallback, "keyword heuristic"
