"""RAG document upload API (user-scoped)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from backend import db
from backend.core.auth import get_request_user
from backend.services.rag import ingest_bytes

router = APIRouter(prefix="/api/files", tags=["rag"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXT = {".txt", ".md", ".markdown", ".pdf", ".csv", ".json", ".py", ".html"}


def _uid(request: Request) -> str:
    user = get_request_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user["id"]


@router.get("")
async def list_files(request: Request) -> dict[str, Any]:
    return {"files": db.list_rag_files(_uid(request))}


@router.post("/upload")
async def upload(file: UploadFile = File(...), request: Request = None) -> dict[str, Any]:
    user_id = _uid(request)
    name = file.filename or "untitled.txt"
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXT)}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File too large (max 5 MB)")
    if not data:
        raise HTTPException(400, "Empty file")

    try:
        result = ingest_bytes(name, data, user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result["chunks"] == 0:
        raise HTTPException(400, "No readable text found in file")
    return result


@router.delete("/{file_id}")
async def delete_file(file_id: str, request: Request) -> dict[str, Any]:
    if not db.delete_rag_file(file_id, _uid(request)):
        raise HTTPException(404, "File not found")
    return {"ok": True}
