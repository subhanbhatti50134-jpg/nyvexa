"""Security regression tests: auth, limits, headers, SSRF, sandbox, uploads, hashing."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import secrets as pysecrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """TestClient with mocked settings + isolated DB + clean rate-limit state."""
    monkeypatch.setenv("NYVEXA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NYVEXA_PASSWORD", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    from backend import db

    importlib.reload(db)
    db.DATA_DIR = tmp_path
    db.DB_PATH = tmp_path / "test.db"
    db.init_db()

    import backend.routers.chat as chat_mod

    class FakeSettings:
        app_name = "Nyvexa"
        api_base = "https://example.invalid/v1"
        api_key = "test-key"
        nvidia_base = "https://example.invalid/nv/v1"
        nvidia_key = ""
        default_model = "m"
        fallback_model = "f"
        max_history = 24
        max_message_chars = 8000
        research_max_queries = 2
        research_results_per_query = 2
        rate_limit_per_minute = 1000
        auth_rate_limit_per_minute = 1000
        cors_origins = ["*"]
        configured = True
        nvidia_configured = False
        openrouter_configured = True

        @property
        def models(self):
            return [{"id": "m", "label": "Mock", "description": ""}]

    monkeypatch.setattr(chat_mod, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(chat_mod, "create_client", lambda *a, **k: object())

    from fastapi.testclient import TestClient

    from backend.main import app

    # reset in-memory limiters so suite order never causes flakes
    for mw in app.user_middleware:
        if mw.cls.__name__ == "RateLimitMiddleware":
            for key in ("limiter", "auth_limiter"):
                mw.kwargs[key]._hits.clear()

    user = db.create_user(email="sec@example.com", password="password123")
    token = db.create_token(user["id"])

    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c

    for mw in app.user_middleware:
        if mw.cls.__name__ == "RateLimitMiddleware":
            for key in ("limiter", "auth_limiter"):
                mw.kwargs[key]._hits.clear()


class TestSecurityHeaders:
    def test_csp_on_frontend_document(self, client):
        r = client.get("/")
        assert r.status_code == 200
        csp = r.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp

    def test_baseline_headers_everywhere(self, client):
        for path in ("/", "/api/health"):
            r = client.get(path)
            assert r.headers.get("x-content-type-options") == "nosniff"
            assert r.headers.get("x-frame-options") == "DENY"
            assert "strict-origin-when-cross-origin" in r.headers.get("referrer-policy", "")

    def test_no_csp_on_api_responses(self, client):
        r = client.get("/api/health")
        assert "content-security-policy" not in r.headers


class TestTokenExtraction:
    def test_query_param_token_rejected(self, client):
        token = client.headers["Authorization"].split(" ", 1)[1]
        # header works
        assert client.get("/api/auth/me").status_code == 200
        # ?token= in the URL must NOT authenticate anymore (no bearer header either)
        anon = client.get(f"/api/auth/me?token={token}", headers={"Authorization": ""})
        assert anon.status_code == 401


class TestOAuthState:
    def test_state_is_random_and_cookie_bound(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
        r = client.get("/api/auth/google", follow_redirects=False)
        assert r.status_code in (302, 307)
        location = r.headers["location"]
        assert "state=" in location
        state = location.split("state=")[1].split("&")[0]
        assert state != "nyvexa" and len(state) > 20
        assert "nyvexa_oauth_state" in r.headers.get("set-cookie", "")

    def test_callback_without_valid_state_rejected(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
        r = client.get(
            "/api/auth/google/callback?code=stolen-code&state=forged",
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "could not be verified" in r.text
        assert "nyvexa_token" not in r.text  # no session issued

    def test_callback_error_is_generic(self, client):
        r = client.get(
            "/api/auth/google/callback?error=access_denied", follow_redirects=False
        )
        assert r.status_code == 200
        assert "cancelled or failed" in r.text


class TestChatValidation:
    def test_oversized_message_rejected(self, client):
        r = client.post("/api/chat", json={"message": "x" * 17000})
        assert r.status_code == 422

    def test_oversized_history_rejected(self, client):
        history = [{"role": "user", "content": "hi"}] * 101
        r = client.post("/api/chat", json={"message": "hello", "history": history})
        assert r.status_code == 422

    def test_invalid_model_id_rejected(self, client):
        r = client.post("/api/chat", json={"message": "hi", "model": "bad model!"})
        assert r.status_code == 200
        assert "Invalid model id" in r.text

    def test_malformed_conversation_id_starts_fresh(self, client):
        r = client.post(
            "/api/chat",
            json={"message": "hi", "mode": "chat", "conversation_id": "not a valid id!"},
        )
        assert r.status_code == 200
        assert "conversation_id" in r.text

    def test_compare_models_validated(self, client):
        r = client.post(
            "/api/chat", json={"message": "hi", "compare_models": ["m", "bad;id!"]}
        )
        assert r.status_code == 200
        assert "Invalid model id" in r.text


class TestRateLimiting:
    def test_auth_endpoints_limited_per_ip(self):
        from backend.core.rate_limit import RateLimiter, RateLimitMiddleware

        async def ok(request):
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/api/auth/login", ok, methods=["POST"])])
        app.add_middleware(
            RateLimitMiddleware, limiter=RateLimiter(100), auth_limiter=RateLimiter(3)
        )
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            for _ in range(3):
                assert c.post("/api/auth/login").status_code == 200
            assert c.post("/api/auth/login").status_code == 429

    def test_chat_limited_and_other_paths_not(self):
        from backend.core.rate_limit import RateLimiter, RateLimitMiddleware

        async def ok(request):
            return JSONResponse({"ok": True})

        app = Starlette(
            routes=[
                Route("/api/chat", ok, methods=["POST"]),
                Route("/api/other", ok, methods=["POST"]),
            ]
        )
        app.add_middleware(
            RateLimitMiddleware, limiter=RateLimiter(2), auth_limiter=RateLimiter(3)
        )
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            assert c.post("/api/chat").status_code == 200
            assert c.post("/api/chat").status_code == 200
            assert c.post("/api/chat").status_code == 429
            assert c.post("/api/other").status_code == 200

    def test_authenticated_requests_keyed_by_user(self):
        from backend.core.rate_limit import RateLimiter, RateLimitMiddleware

        captured: list[str] = []

        class SpyLimiter(RateLimiter):
            def allow(self, key: str) -> bool:
                captured.append(key)
                return True

        mw = RateLimitMiddleware(app=None, limiter=SpyLimiter(100), auth_limiter=SpyLimiter(100))

        async def dispatch(user_id: str | None):
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/api/chat",
                "headers": [],
                "client": ("9.9.9.1", 1),
                "state": {},
            }
            if user_id:
                scope["state"]["user_id"] = user_id
            request = Request(scope)

            async def call_next(_req):
                return JSONResponse({"ok": True})

            await mw.dispatch(request, call_next)

        asyncio.run(dispatch("user-abc"))
        asyncio.run(dispatch(None))
        assert captured == ["user:user-abc", "ip:9.9.9.1"]


class TestBodyLimit:
    def test_oversized_body_413(self, client):
        r = client.post(
            "/api/auth/login",
            content=b"x" * (7 * 1024 * 1024),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413

    def test_normal_body_still_works(self, client):
        r = client.post(
            "/api/auth/login", json={"email": "sec@example.com", "password": "password123"}
        )
        assert r.status_code == 200


class TestFetcherSSRF:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://localhost:8300/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/internal",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://100.64.1.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "file:///etc/passwd",
            "ftp://1.1.1.1/x",
            "http://user@8.8.8.8/",
        ],
    )
    def test_blocked_targets(self, url):
        from backend.services.fetcher import url_is_public

        assert url_is_public(url) is False

    def test_public_numeric_host_allowed(self):
        from backend.services.fetcher import url_is_public

        assert url_is_public("https://8.8.8.8/") is True


class TestRunPythonSandbox:
    @pytest.mark.parametrize(
        "code",
        [
            "import importlib; importlib.import_module('os')",
            "from os import system; print(1)",
            "import  os; print(os.getcwd())",
            "print((1).__class__.__bases__)",
            "print(getattr(1, '__class__'))",
            "print(open('/etc/passwd').read())",
        ],
    )
    def test_bypasses_blocked(self, code):
        from backend.services.tools import run_python

        assert '"error"' in run_python(code)

    def test_legit_code_runs(self):
        import json as _json

        from backend.services.tools import run_python

        out = _json.loads(run_python("print(2 + 2)"))
        assert out["stdout"].strip() == "4"
        assert out["returncode"] == 0


class TestUploadValidation:
    def test_pdf_extension_requires_pdf_magic(self, client):
        files = {"file": ("fake.pdf", b"definitely not a pdf", "application/pdf")}
        r = client.post("/api/files/upload", files=files)
        assert r.status_code == 400
        assert "PDF" in r.json()["detail"]

    def test_text_file_rejects_nul_bytes(self, client):
        files = {"file": ("notes.txt", b"hello\x00world", "text/plain")}
        r = client.post("/api/files/upload", files=files)
        assert r.status_code == 400

    def test_valid_text_upload_ok(self, client):
        payload = b"Nyvexa knowledge base entry about streaming responses over SSE."
        files = {"file": ("notes.txt", payload, "text/plain")}
        r = client.post("/api/files/upload", files=files)
        assert r.status_code == 200
        assert r.json()["chunks"] >= 1


class TestPasswordRehash:
    def test_old_hash_upgraded_on_login(self, client):
        from backend import db

        user = db.create_user(email="oldhash@example.com", password="password123")
        # simulate a legacy low-cost hash
        salt = pysecrets.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", b"password123", salt, 100_000)
        old = f"pbkdf2$100000${salt.hex()}${dk.hex()}"
        with db.get_db() as conn:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?", (old, user["id"])
            )
        assert db.password_needs_rehash(old) is True

        r = client.post(
            "/api/auth/login", json={"email": "oldhash@example.com", "password": "password123"}
        )
        assert r.status_code == 200

        row = db.get_user_by_email("oldhash@example.com")
        assert row["password_hash"].startswith(f"pbkdf2${db._PBKDF2_ITERATIONS}$")
        assert db.verify_password("password123", row["password_hash"]) is True
        assert db.password_needs_rehash(row["password_hash"]) is False
