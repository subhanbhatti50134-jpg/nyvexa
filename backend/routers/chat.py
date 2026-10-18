"""Nyvexa chat API — SSE streaming with modes: chat | research | deep | agent | compare."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from openai import APIError
from pydantic import BaseModel, Field

from backend import db
from backend.core.config import get_settings
from backend.core.context import set_memory_block, set_user_id
from backend.services.agent import stream_agent
from backend.services.llm import create_client
from backend.services.memory import extract_memories
from backend.services.rag import retrieve as rag_retrieve
from backend.services.research import run_chat_only, stream_research
from backend.services.router import route_mode

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger("nyvexa.chat")

# Strict shape for model ids / conversation ids before they reach providers or SQL
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
CONV_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Keep strong refs to fire-and-forget tasks so they aren't garbage-collected.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


class ChatTurn(BaseModel):
    role: str = Field(..., max_length=20)
    content: str = Field(..., max_length=8000)


class ChatRequest(BaseModel):
    # Hard request-size caps (message is further trimmed to settings.max_message_chars)
    message: str = Field(..., min_length=1, max_length=16000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=100)
    model: str | None = Field(default=None, max_length=120)
    research: bool = False
    mode: str = Field(default="auto", max_length=16)  # auto | chat | research | deep | agent
    conversation_id: str | None = Field(default=None, max_length=64)
    use_kb: bool = False  # attach RAG context in chat mode
    compare_models: list[str] | None = Field(default=None, max_length=2)


def _valid_model(model_id: str | None) -> bool:
    return model_id is None or bool(MODEL_ID_RE.match(model_id))


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize_mode(req: ChatRequest) -> str:
    if req.compare_models and len(req.compare_models) >= 2:
        return "compare"
    mode = (req.mode or "").strip().lower()
    if mode not in {"auto", "chat", "research", "deep", "agent"}:
        mode = "research" if req.research else "auto"
    return mode


async def _run_single(
    mode: str,
    client,
    settings,
    message: str,
    history: list[dict[str, str]],
    model: str | None,
    emit,
    slot: str | None = None,
    rag_context: str = "",
) -> None:
    """Run one generation mode; emit SSE events (optionally tagged with a slot)."""

    async def tagged(event: str, data: dict[str, Any]) -> None:
        if slot:
            data = {**data, "slot": slot}
        await emit(event, data)

    if mode == "agent":
        async for token in stream_agent(client, settings, message, history, model, tagged):
            await emit("token", {"text": token, **({"slot": slot} if slot else {})})
        return
    if mode == "research":
        async for token in stream_research(
            client, settings, message, history, model, tagged, deep=False
        ):
            await emit("token", {"text": token, **({"slot": slot} if slot else {})})
        return
    if mode == "deep":
        async for token in stream_research(
            client, settings, message, history, model, tagged, deep=True
        ):
            await emit("token", {"text": token, **({"slot": slot} if slot else {})})
        return
    # chat
    async for token in run_chat_only(
        client, settings, message, history, model, tagged, rag_context=rag_context
    ):
        await emit("token", {"text": token, **({"slot": slot} if slot else {})})
    # run_chat_only already emits done


async def _event_stream(req: ChatRequest, request: Request) -> AsyncIterator[str]:
    settings = get_settings()
    user = getattr(request.state, "user", None)
    user_id = user["id"] if user else None
    set_user_id(user_id)
    set_memory_block(db.memory_block(user_id) if user_id else None)

    if not settings.configured:
        yield _sse(
            "error",
            {
                "message": "No model provider is configured. Copy backend/.env.example to "
                "backend/.env and set NVIDIA_API_KEY or OPENROUTER_API_KEY "
                "(https://openrouter.ai/keys)"
            },
        )
        return

    message = req.message.strip()[: settings.max_message_chars]
    if not message:
        yield _sse("error", {"message": "Empty message"})
        return

    history = [
        {"role": t.role, "content": t.content[:4000]}
        for t in req.history
        if t.role in ("user", "assistant") and t.content.strip()
    ][-settings.max_history :]

    mode = _normalize_mode(req)
    if not _valid_model(req.model):
        yield _sse("error", {"message": "Invalid model id."})
        return
    if req.compare_models and not all(_valid_model(m) for m in req.compare_models):
        yield _sse("error", {"message": "Invalid model id in compare selection."})
        return
    client = create_client(settings, model=req.model or settings.default_model)

    # Auto mode: let a tiny router pick chat | research | deep | agent
    auto_reason: str | None = None
    if mode == "auto":
        mode, auto_reason = await route_mode(client, settings, message)

    # Persistence: create or reuse conversation (user-owned)
    if not user_id:
        yield _sse("error", {"message": "Unauthorized — please log in"})
        return

    conv_id = req.conversation_id
    if conv_id and not CONV_ID_RE.match(conv_id):
        conv_id = None  # malformed client-supplied id — start a fresh conversation
    if conv_id:
        conv = db.get_conversation(conv_id, user_id=user_id)
        if not conv:
            conv = db.create_conversation(user_id=user_id, title=message[:60], model=req.model, mode=mode)
            conv_id = conv["id"]
    else:
        conv = db.create_conversation(user_id=user_id, title=message[:60], model=req.model, mode=mode)
        conv_id = conv["id"]

    db.add_message(conv_id, "user", message, mode=mode)

    # RAG context for chat mode
    rag_context = ""
    if mode == "chat" and req.use_kb:
        try:
            hits = await rag_retrieve(message, top_k=4, user_id=user_id)
            if hits:
                rag_context = "\n\n".join(
                    f"[{h['file']} #{h['chunk_index']}]\n{h['text']}" for h in hits
                )
        except Exception:
            rag_context = ""

    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def producer() -> None:
        try:
            if mode == "compare":
                models = (req.compare_models or [])[:2]
                await emit(
                    "meta",
                    {"mode": mode, "models": models, "conversation_id": conv_id},
                )
                use_mode = req.mode if req.mode in {"chat", "research", "agent"} else "chat"
                slot_texts: dict[str, list[str]] = {"a": [], "b": []}

                async def run_slot(slot: str, model_id: str) -> None:
                    async def wrap_emit(event: str, data: dict[str, Any]) -> None:
                        if event == "done":
                            return  # per-slot done handled below
                        await emit(event, {**data, "slot": slot})

                    async def feed(token: str) -> None:
                        slot_texts[slot].append(token)
                        await emit("token", {"text": token, "slot": slot})

                    if use_mode == "research":
                        async for token in stream_research(
                            client, settings, message, history, model_id, wrap_emit, deep=False
                        ):
                            await feed(token)
                    elif use_mode == "agent":
                        async for token in stream_agent(
                            client, settings, message, history, model_id, wrap_emit
                        ):
                            await feed(token)
                    else:
                        async for token in run_chat_only(
                            client, settings, message, history, model_id, wrap_emit
                        ):
                            await feed(token)
                    await emit(
                        "done",
                        {"mode": "compare", "slot": slot, "model": model_id},
                    )

                await asyncio.gather(
                    run_slot("a", models[0]),
                    run_slot("b", models[1]),
                    return_exceptions=True,
                )
                combined = "\n\n".join(
                    f"### {m}\n{''.join(slot_texts[s])}"
                    for s, m in zip(("a", "b"), models, strict=False)
                )
                db.add_message(conv_id, "assistant", combined.strip(), mode="compare")
                await emit("done", {"mode": "compare", "conversation_id": conv_id})
                return

            # Single-model modes (meta already yielded to the client above)
            buffer: list[str] = []
            usage: dict[str, Any] = {}

            async def collect(event: str, data: dict[str, Any]) -> None:
                if event == "token" and data.get("text"):
                    buffer.append(data["text"])
                if event == "done" and data.get("usage"):
                    usage.update(data["usage"])
                await emit(event, data)

            await _run_single(
                mode, client, settings, message, history, req.model, collect,
                rag_context=rag_context,
            )

            full_text = "".join(buffer)
            meta = {"usage": usage} if usage else {}
            db.add_message(conv_id, "assistant", full_text, mode=mode, meta=meta)
            if conv_id:
                db.touch_conversation(conv_id)
            # Learn durable facts about the user in the background (best-effort)
            if user_id and full_text:
                _spawn(extract_memories(client, settings, user_id, message, full_text))

        except APIError as exc:
            # Full provider detail goes to server logs only; client gets a bounded,
            # control-char-free message (provider text can be long/noisy but never
            # contains our credentials).
            logger.warning("provider APIError: %s", exc)
            detail = re.sub(r"[\r\n]+", " ", str(getattr(exc, "message", "") or exc))[:300]
            await emit(
                "error",
                {"message": f"Model provider error: {detail}" if detail else "Model provider error."},
            )
        except Exception:
            logger.exception("chat stream failed")
            await emit("error", {"message": "Something went wrong on our side — please try again."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(producer())

    yield _sse(
        "meta",
        {
            "mode": mode,
            "model": req.model or settings.default_model,
            "conversation_id": conv_id,
            **({"auto_reason": auto_reason} if auto_reason else {}),
            **({"models": req.compare_models} if mode == "compare" else {}),
        },
    )
    try:
        while True:
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({get_task}, timeout=1.0)
            if not done:
                get_task.cancel()
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
                continue
            item = get_task.result()
            if item is None:
                break
            event, data = item
            if event == "done":
                if data.get("mode") == "compare" and data.get("slot"):
                    # per-slot completion — keep streaming the other slot
                    yield _sse(event, data)
                    continue
                data.setdefault("conversation_id", conv_id)
                yield _sse(event, data)
                break
            yield _sse(event, data)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(req, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models")
async def list_models() -> dict[str, Any]:
    settings = get_settings()
    return {"models": settings.models, "default": settings.default_model}


@router.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    from backend.core.auth import google_configured

    return {
        "status": "ok",
        "app": settings.app_name,
        "openrouter_configured": settings.openrouter_configured,
        "nvidia_configured": settings.nvidia_configured,
        "auth_required": True,
        "google_enabled": google_configured(),
    }
