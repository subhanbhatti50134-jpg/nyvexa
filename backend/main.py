"""Nyvexa — FastAPI application entrypoint.

Run:  uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import db
from backend.core.auth import AuthMiddleware
from backend.core.config import get_settings
from backend.core.rate_limit import RateLimiter, RateLimitMiddleware
from backend.routers.auth import router as auth_router
from backend.routers.chat import router as chat_router
from backend.routers.files import router as files_router
from backend.routers.history import router as history_router
from backend.routers.memory import router as memory_router
from backend.services import hf_sync, keepalive

settings = get_settings()
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks: list[asyncio.Task] = []
    if hf_sync.enabled():
        await asyncio.to_thread(hf_sync.pull_db)  # restore DB on fresh HF boots
        db.init_db()  # re-run schema/migrations on the restored file
        tasks.append(asyncio.create_task(hf_sync.sync_loop()))
    if keepalive.enabled():
        tasks.append(asyncio.create_task(keepalive.loop()))
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    if hf_sync.enabled():
        await asyncio.to_thread(hf_sync.push_db, True)  # final push on shutdown


db.init_db()

app = FastAPI(
    title="Nyvexa", version="2.0.0", docs_url="/api/docs", lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.rate_limit_per_minute))
app.add_middleware(AuthMiddleware)

app.include_router(chat_router)
app.include_router(history_router)
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(memory_router)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    """Always revalidate static assets — heuristic caching served stale JS
    (an old main.js caused 422 login posts after the auth UI changed)."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in {"/manifest.json", "/sw.js"}:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/manifest.json", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "sw.js", media_type="application/javascript")
