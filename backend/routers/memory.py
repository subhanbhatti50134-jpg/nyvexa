"""Memory API — view, delete, and clear the user's long-term memory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import db

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
async def list_memory(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not logged in")
    return {"memories": db.list_memories(user["id"])}


@router.delete("")
async def clear_memory(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not logged in")
    removed = db.clear_memories(user["id"])
    return {"ok": True, "removed": removed}


@router.delete("/{memory_id}")
async def delete_one(memory_id: str, request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not logged in")
    if not db.delete_memory(memory_id, user["id"]):
        raise HTTPException(404, "Memory not found")
    return {"ok": True}
