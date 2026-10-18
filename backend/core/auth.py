"""Auth: sessions, password login, Google OAuth, request middleware."""

from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import urlencode

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend import db

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/google",
    "/api/auth/google/callback",
    "/api/auth/status",
    "/api/health",
    "/api/models",
    "/",
}


def google_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID", "").strip()) and bool(
        os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    )


def redirect_uri_for(request: Request) -> str:
    """Resolve the Google OAuth redirect URI from the request origin.

    The same deployment is reachable from several hosts (localhost, ngrok
    tunnel, LAN IP) and Google requires an exact redirect_uri match, so derive
    it per-request. GOOGLE_REDIRECT_URI pins it explicitly when set.
    """
    explicit = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    origin = str(request.base_url).rstrip("/")
    return f"{origin}/api/auth/google/callback"


def extract_token(request: Request) -> str:
    """Bearer header only — query-string tokens leak via URLs/logs/referers."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def get_request_user(request: Request) -> dict | None:
    """Resolve the authenticated user for this request (None if anonymous)."""
    token = extract_token(request)
    if not token:
        return None
    user_id = db.token_user(token)
    if not user_id:
        return None
    return db.get_user(user_id)


def issue_session(user_id: str) -> str:
    return db.create_token(user_id)


# ---------- Google OAuth ----------

def google_auth_url(state: str, redirect_uri: str) -> str:
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def google_exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens, then fetch userinfo. Returns profile dict."""
    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise ValueError(f"Google token exchange failed ({token_resp.status_code})")
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            raise ValueError("No access token from Google")

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if userinfo_resp.status_code != 200:
            raise ValueError(f"Google userinfo failed ({userinfo_resp.status_code})")
        info = userinfo_resp.json()
        email = (info.get("email") or "").strip().lower()
        if not email:
            raise ValueError("Google account has no email")
        return {
            "sub": info.get("sub", ""),
            "email": email,
            "name": info.get("name") or info.get("given_name") or "",
            "picture": info.get("picture") or "",
        }


def upsert_google_user(profile: dict) -> dict:
    """Find or create a user by Google sub (merge by email if needed)."""
    user = db.get_user_by_google_sub(profile["sub"])
    if user:
        return user
    user = db.get_user_by_email(profile["email"])
    if user:
        db.link_google_sub(user["id"], profile["sub"], profile.get("name", ""))
        return db.get_user(user["id"]) or user
    return db.create_user(
        email=profile["email"],
        password=None,
        name=profile.get("name", ""),
        google_sub=profile["sub"],
    )


# ---------- middleware ----------

class AuthMiddleware(BaseHTTPMiddleware):
    """Require a logged-in user on all /api/* routes except public auth paths."""

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path in PUBLIC_PATHS:
            return await call_next(request)

        token = extract_token(request)
        user_id = db.token_user(token) if token else None
        if not user_id:
            return JSONResponse(status_code=401, content={"error": "Unauthorized — please log in"})

        user = db.get_user(user_id)
        if not user:
            return JSONResponse(status_code=401, content={"error": "Unauthorized — account not found"})

        # Stash user on request.state for handlers
        request.state.user = user
        request.state.user_id = user["id"]
        return await call_next(request)
