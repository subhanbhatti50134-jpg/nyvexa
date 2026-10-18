"""Rate limiting: in-memory sliding window, optional Redis when REDIS_URL is set."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PROTECTED_PREFIXES = ("/api/chat", "/api/files")
AUTH_PATHS = ("/api/auth/login", "/api/auth/signup")


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._redis = None
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

    def allow(self, key: str) -> bool:
        if self._redis is not None:
            return self._allow_redis(key)
        return self._allow_memory(key)

    def _allow_memory(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True

    def _allow_redis(self, key: str) -> bool:
        """Sliding window via sorted set."""
        try:
            now = time.time()
            rkey = f"nyvexa:rl:{key}"
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(rkey, 0, now - self.window)
            pipe.zadd(rkey, {f"{now}:{os.urandom(4).hex()}": now})
            pipe.zcard(rkey)
            pipe.expire(rkey, int(self.window) + 5)
            results = pipe.execute()
            count = results[2]
            return count <= self.max_requests
        except Exception:
            return self._allow_memory(key)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limits.

    - Auth endpoints (login/signup): strict per-IP cap against credential stuffing.
    - Expensive endpoints (chat/files): per-ACCOUNT cap when authenticated
      (falls back to per-IP for anonymous traffic). AuthMiddleware runs first
      (outermost), so request.state.user_id is already populated here.
    """

    def __init__(self, app, limiter: RateLimiter, auth_limiter: RateLimiter | None = None) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.auth_limiter = auth_limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        client = request.client.host if request.client else "unknown"

        if self.auth_limiter is not None and path in AUTH_PATHS:
            if not self.auth_limiter.allow(f"ip:{client}"):
                return JSONResponse(
                    status_code=429,
                    content={"error": "Too many attempts — please wait a minute and try again."},
                )
        elif any(path.startswith(p) for p in PROTECTED_PREFIXES):
            user_id = getattr(request.state, "user_id", None)
            key = f"user:{user_id}" if user_id else f"ip:{client}"
            if not self.limiter.allow(key):
                return JSONResponse(
                    status_code=429,
                    content={"error": "Rate limit exceeded. Please wait a moment and try again."},
                )
        return await call_next(request)
