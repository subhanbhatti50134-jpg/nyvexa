"""Nyvexa — FastAPI application entrypoint.

Run:  uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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
app.add_middleware(
    RateLimitMiddleware,
    limiter=RateLimiter(settings.rate_limit_per_minute),
    auth_limiter=RateLimiter(settings.auth_rate_limit_per_minute),
)
app.add_middleware(AuthMiddleware)

app.include_router(chat_router)
app.include_router(history_router)
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(memory_router)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# CSP: strict for the frontend document (no inline scripts/styles anywhere in it).
# /api/* responses are JSON/SSE or the OAuth bridge pages (server-authored HTML with
# inline bootstrap scripts) and swagger-ui assets — CSP is not applied there.
_CSP = (
    "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
    "object-src 'none'; script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; connect-src 'self'; "
    "manifest-src 'self'; worker-src 'self'"
)


class BodyLimitMiddleware:
    """Reject oversized request bodies (uploads are capped at 5 MB)."""

    def __init__(self, app, max_bytes: int = 6 * 1024 * 1024) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        content_length = headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > self.max_bytes:
            await self._send_413(send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    # surfaces through FastAPI's exception handler as a clean 413
                    raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _send_413(send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


@app.middleware("http")
async def security_and_cache_headers(request: Request, call_next):
    """Static revalidation + baseline security headers on every response."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in {"/manifest.json", "/sw.js"}:
        # heuristic caching served stale JS before (old main.js -> 422 logins)
        response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=(), payment=(), microphone=(self)"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if not path.startswith("/api/"):
        response.headers["Content-Security-Policy"] = _CSP
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


# Added last on purpose: add_middleware prepends, so this is the outermost layer
# (the 413 short-circuit happens before any other middleware runs).
app.add_middleware(BodyLimitMiddleware, max_bytes=6 * 1024 * 1024)
