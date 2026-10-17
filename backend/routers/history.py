"""Conversation history API (user-scoped)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend import db
from backend.core.auth import get_request_user

router = APIRouter(prefix="/api/conversations", tags=["history"])


class RenameBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


def _uid(request: Request) -> str:
    user = get_request_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user["id"]


@router.get("")
async def list_conversations(request: Request) -> dict[str, Any]:
    return {"conversations": db.list_conversations(_uid(request))}


@router.post("")
async def create_conversation(request: Request, body: RenameBody | None = None) -> dict[str, Any]:
    title = body.title if body else "New chat"
    return db.create_conversation(user_id=_uid(request), title=title)


@router.get("/{conv_id}")
async def get_conversation(conv_id: str, request: Request) -> dict[str, Any]:
    conv = db.get_conversation(conv_id, user_id=_uid(request))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = db.get_messages(conv_id)
    return {"conversation": conv, "messages": messages}


@router.patch("/{conv_id}")
async def rename(conv_id: str, body: RenameBody, request: Request) -> dict[str, Any]:
    if not db.rename_conversation(conv_id, body.title, _uid(request)):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True, "title": body.title}


@router.delete("/{conv_id}")
async def delete(conv_id: str, request: Request) -> dict[str, Any]:
    if not db.delete_conversation(conv_id, _uid(request)):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


@router.delete("/{conv_id}/messages/after/{message_id}")
async def truncate_after(conv_id: str, message_id: str, request: Request) -> dict[str, Any]:
    conv = db.get_conversation(conv_id, user_id=_uid(request))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    removed = db.delete_messages_after(conv_id, message_id)
    return {"ok": True, "removed": removed}
