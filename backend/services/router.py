"""Auto mode — a tiny router that picks chat | research | deep | agent per question."""

from __future__ import annotations

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
_KW_AGENT = re.compile(r"\b(run|execute|calculate|compute|script|python|scrape|fetch https?://)\b", re.I)


def _heuristic(question: str) -> str:
    if _KW_AGENT.search(question):
        return "agent"
    if _KW_TIME.search(question):
        return "research"
    if _KW_DEEP.search(question):
        return "deep"
    return "chat"


async def route_mode(
    client: AsyncOpenAI | None, settings: Settings, question: str
) -> tuple[str, str]:
    """Return (mode, reason). Falls back to keyword heuristics on any failure."""
    fallback = _heuristic(question)
    try:
        data: Any = await complete_json(
            client,
            settings,
            [
                {
                    "role": "user",
                    "content": ROUTER_PROMPT.replace("__QUESTION__", question[:400]),
                }
            ],
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
