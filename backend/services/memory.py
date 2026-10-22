"""Long-term user memory — extract durable facts after each assistant turn.

Extraction is intentionally conservative: only facts that will be true next week
(name, role, standing preferences, ongoing projects). Never stores secrets.
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from backend import db
from backend.core.config import Settings
from backend.services.llm import complete_json

EXTRACT_PROMPT = """\
You maintain a small persistent memory file for a user of an AI assistant.
Read the exchange and extract durable, reusable facts about the user.

Store ONLY facts that:
- will likely still be true next week (name, job/role, home city/timezone,
  standing preferences, ongoing projects, frequently used tools/languages)
- would let the assistant be more useful in FUTURE unrelated conversations

Never store: passwords, API keys, tokens, credit cards, one-off requests,
transient emotions, or anything the user did not reveal about themselves.

Output ONLY a JSON array of short strings (each ≤ 12 words), or [] if nothing
qualifies. No prose, no markdown fence.

Known facts already stored (do not repeat these):
{known}

Latest exchange:
User: {user}
Assistant: {assistant}
"""

_SECRET_RE = re.compile(
    r"(password|secret|api[_ ]?key|token|credential|card number|otp)\s*[:=]", re.I
)


def _safe(text: str) -> bool:
    return bool(text.strip()) and not _SECRET_RE.search(text)


async def extract_memories(
    client: AsyncOpenAI | None, settings: Settings, user_id: str, user_msg: str, assistant_msg: str
) -> list[str]:
    """Extract and persist new facts. Returns the facts that were added."""
    if not user_id or len(user_msg) + len(assistant_msg) < 80:
        return []
    known = db.memory_block(user_id, max_items=20) or "(none)"
    try:
        data: Any = await complete_json(
            client,
            settings,
            [
                {
                    "role": "user",
                    "content": EXTRACT_PROMPT.format(
                        known=known, user=user_msg[:1500], assistant=assistant_msg[:1500]
                    ),
                }
            ],
            # Background work on the small model — keeps the default model's
            # concurrency slots (NIM free tier caps at 16/worker) for answers.
            model=getattr(settings, "fast_model", None) or None,
        )
    except Exception:
        return []

    items: list[str] = []
    if isinstance(data, list):
        items = [str(x) for x in data if isinstance(x, str)]
    elif isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                items = [str(x) for x in parsed if isinstance(x, str)]
        except Exception:
            return []

    added: list[str] = []
    for item in items[:3]:  # conservative: at most 3 new facts per turn
        item = item.strip()[:200]
        if _safe(item) and db.add_memory(user_id, item):
            added.append(item)
    return added
