"""Auth API: email signup/login, Google OAuth, session management."""

from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend import db
from backend.core.auth import (
    google_auth_url,
    google_configured,
    google_exchange_code,
    issue_session,
    redirect_uri_for,
    upsert_google_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
OAUTH_STATE_COOKIE = "nyvexa_oauth_state"
OAUTH_STATE_TTL = 600

_BODY_STYLE = (
    "background:#f7f6f2;color:#102747;font-family:sans-serif;"
    "display:grid;place-items:center;height:100vh;margin:0"
)


class SignupBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=MIN_PASSWORD, max_length=128)
    name: str = Field(default="", max_length=80)


class LoginBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "email": user["email"], "name": user.get("name") or ""}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    from backend.core.auth import extract_token

    token = extract_token(request)
    user_id = db.token_user(token) if token else None
    user = db.get_user(user_id) if user_id else None
    return {
        "auth_required": True,
        "google_enabled": google_configured(),
        "user": _public_user(user) if user else None,
    }


@router.post("/signup")
async def signup(body: SignupBody) -> dict[str, Any]:
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address")
    if len(body.password) < MIN_PASSWORD:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists — try logging in")
    user = db.create_user(email=email, password=body.password, name=body.name)
    token = issue_session(user["id"])
    return {"token": token, "user": _public_user(user)}


@router.post("/login")
async def login(body: LoginBody) -> dict[str, Any]:
    user = db.get_user_by_email(body.email)
    if not user or not user.get("password_hash"):
        raise HTTPException(401, "Invalid email or password")
    if not db.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    # transparent cost upgrade: old hashes are re-saved with current iterations
    if db.password_needs_rehash(user["password_hash"]):
        try:
            db.set_password(user["email"], body.password)
        except Exception:
            pass  # never fail a valid login over a rehash
    token = issue_session(user["id"])
    return {"token": token, "user": _public_user(user)}


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    from backend.core.auth import extract_token

    token = extract_token(request)
    if token:
        db.revoke_token(token)
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not logged in")
    return {"user": _public_user(user)}


# ---------- Google OAuth ----------

@router.get("/google")
async def google_start(request: Request) -> RedirectResponse:
    if not google_configured():
        raise HTTPException(
            400,
            "Google login is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in backend/.env",
        )
    # Random per-attempt state bound to this browser (CSRF protection)
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(google_auth_url(state, redirect_uri_for(request)))
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=OAUTH_STATE_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
) -> HTMLResponse:
    # Always return to the exact origin the browser used (localhost vs ngrok
    # tunnel vs LAN IP) — the session token is written to localStorage on THAT origin.
    origin = str(request.base_url).rstrip("/")

    expected = request.cookies.get(OAUTH_STATE_COOKIE, "")

    def done(resp: HTMLResponse) -> HTMLResponse:
        resp.delete_cookie(OAUTH_STATE_COOKIE, samesite="lax")
        return resp

    if error or not code:
        msg = "Google sign-in was cancelled or failed."
        _log_google("denied", None, msg)
        return done(_redirect_html(origin, ok=False, message=msg))

    # Validate the state echo (CSRF); never trust the code without it
    if not expected or not state or not secrets.compare_digest(expected, state):
        _log_google("state_mismatch", None, "oauth state missing or mismatched")
        return done(
            _redirect_html(
                origin, ok=False, message="Google sign-in could not be verified — please try again."
            )
        )

    try:
        profile = await google_exchange_code(code, redirect_uri_for(request))
        user = upsert_google_user(profile)
        token = issue_session(user["id"])
    except Exception as exc:
        _log_google("error", None, str(exc))
        # raw exception stays in server logs only — generic message to the browser
        return done(
            _redirect_html(
                origin, ok=False, message="Google sign-in failed — please try again."
            )
        )

    _log_google("ok", user.get("email", ""), f"google_sub={profile.get('sub', '')}")
    # Token delivered via a short-lived bridge page (never appears in server logs
    # or the final URL); redirect stays on the caller's origin.
    return done(
        HTMLResponse(
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Nyvexa</title>
        <script>
          try {{
            localStorage.setItem("nyvexa_token", {token!r});
            localStorage.setItem("nyvexa_user", {user["email"]!r});
            sessionStorage.removeItem("nyvexa_reauth");
            sessionStorage.removeItem("nyvexa_auth_msg");
          }} catch (e) {{}}
          window.location.replace({origin!r});
        </script></head><body style="{_BODY_STYLE}">
        <p>Signing you in…</p></body></html>""",
            status_code=200,
        )
    )


def _log_google(event: str, email: str | None, detail: str) -> None:
    import logging

    logging.getLogger("nyvexa.auth").info(
        "google_oauth %s email=%s %s", event, email or "-", detail
    )


def _redirect_html(app_url: str, ok: bool, message: str) -> HTMLResponse:
    import json as _json

    payload = _json.dumps({"ok": ok, "message": message})
    return HTMLResponse(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Nyvexa</title>
        <script>
          sessionStorage.setItem("nyvexa_auth_msg", {payload});
          window.location.replace({app_url!r});
        </script></head><body style="{_BODY_STYLE}">
        <p>{message}</p></body></html>"""
    )
