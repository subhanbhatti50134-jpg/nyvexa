"""Nyvexa chat API — SSE streaming with modes: chat | research | deep | agent | compare."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from openai import APIError
from pydantic import BaseModel, Field

from backend import db
from backend.core.config import get_settings
from backend.core.context import set_user_id
from backend.services.agent import stream_agent
from backend.services.llm import create_client
from backend.services.rag import retrieve as rag_retrieve
from backend.services.research import run_chat_only, stream_research

router = APIRouter(prefix="/api", tags=["chat"])


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: list[ChatTurn] = Field(default_factory=list)
    model: str | None = None
    research: bool = False
    mode: str = Field(default="chat")  # chat | research | deep | agent
    conversation_id: str | None = None
    use_kb: bool = False  # attach RAG context in chat mode
    compare_models: list[str] | None = None  # enables compare mode


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize_mode(req: ChatRequest) -> str:
    if req.compare_models and len(req.compare_models) >= 2:
        return "compare"
    mode = (req.mode or "").strip().lower()
    if mode not in {"chat", "research", "deep", "agent"}:
        mode = "research" if req.research else "chat"
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

    if not settings.configured:
        yield _sse(
            "error",
            {
                "message": "OPENROUTER_API_KEY is not set. Copy backend/.env.example to "
                "backend/.env and add your key from https://openrouter.ai/keys"
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
    client = create_client(settings, model=req.model or settings.default_model)

    # Persistence: create or reuse conversation (user-owned)
    if not user_id:
        yield _sse("error", {"message": "Unauthorized — please log in"})
        return

    conv_id = req.conversation_id
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

        except APIError as exc:
            await emit("error", {"message": f"Model provider error: {exc.message}"})
        except Exception as exc:
            await emit("error", {"message": f"Unexpected server error: {exc}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(producer())

    yield _sse(
        "meta",
        {
            "mode": mode,
            "model": req.model or settings.default_model,
            "conversation_id": conv_id,
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
