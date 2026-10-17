"""API tests for Nyvexa (mocked LLM + search — no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.research import Source


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """TestClient with mocked LLM/search and isolated DB."""

    monkeypatch.setenv("NYVEXA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NYVEXA_PASSWORD", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    # Re-import db with new data dir
    import importlib

    from backend import db

    importlib.reload(db)
    db.DATA_DIR = tmp_path
    db.DB_PATH = tmp_path / "test.db"
    db.init_db()

    import backend.services.research as R
    import backend.services.websearch as W

    async def fake_complete_json(*a, **k):
        return ["FastAPI SSE"]

    async def fake_stream_chat(*a, **k):
        for tok in ["Hello ", "Nyvexa"]:
            yield tok

    def fake_search(queries, per=4):
        return [
            Source(
                title="FastAPI SSE",
                url="https://fastapi.tiangolo.com/tutorial/server-sent-events/",
                snippet="SSE docs",
                query=queries[0] if queries else "",
            )
        ]

    monkeypatch.setattr(R, "complete_json", fake_complete_json)
    monkeypatch.setattr(R, "_stream_with_usage", _fake_stream_with_usage)
    monkeypatch.setattr(W, "search_sync", fake_search)

    import backend.services.research as research_mod

    monkeypatch.setattr(research_mod, "search_sync", fake_search)

    # Make settings appear configured so /api/chat doesn't short-circuit
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
        cors_origins = ["*"]
        configured = True
        nvidia_configured = False
        openrouter_configured = True

        @property
        def models(self):
            return [{"id": "m", "label": "Mock", "description": ""}]

    import backend.routers.chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(chat_mod, "create_client", lambda *a, **k: object())

    from fastapi.testclient import TestClient

    from backend.main import app

    # Seed a test user and authenticate the client
    user = db.create_user(email="tester@example.com", password="password123")
    token = db.create_token(user["id"])

    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


async def _fake_stream_with_usage(client, settings, messages, model):
    for tok in ["Grounded ", "answer [1]."]:
        yield ("token", tok)
    yield (
        "usage",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": model},
    )


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_models(self, client):
        r = client.get("/api/models")
        assert r.status_code == 200
        data = r.json()
        assert len(data["models"]) >= 1
        assert "default" in data

    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Nyvexa" in r.text


class TestChatSSE:
    def _events(self, resp):
        events = []
        for line in resp.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
        return events

    def test_chat_mode(self, client, monkeypatch):
        import backend.routers.chat as chat_mod

        async def fake_run_chat_only(client, settings, q, h, m, emit, rag_context=""):
            await emit("research", {"step": "chat", "message": "Thinking…"})
            yield "Hi "
            yield "there"
            await emit("done", {"mode": "chat", "usage": {}})

        monkeypatch.setattr(chat_mod, "run_chat_only", fake_run_chat_only)

        with client.stream(
            "POST", "/api/chat", json={"message": "hello", "mode": "chat"}
        ) as resp:
            assert resp.status_code == 200
            events = self._events(resp)
        assert "meta" in events
        assert "token" in events
        assert "done" in events

    def test_research_mode(self, client, monkeypatch):
        import backend.routers.chat as chat_mod

        async def fake_stream_research(client, settings, q, h, m, emit, deep=False):
            await emit("research", {"step": "planning", "message": "Planning…"})
            await emit(
                "sources",
                {
                    "sources": [
                        {
                            "index": 1,
                            "title": "t",
                            "url": "https://example.com",
                            "snippet": "s",
                            "query": "q",
                        }
                    ]
                },
            )
            yield "Answer [1]"
            await emit("done", {"mode": "research", "usage": {}})

        monkeypatch.setattr(chat_mod, "stream_research", fake_stream_research)

        with client.stream(
            "POST", "/api/chat", json={"message": "what is x", "mode": "research"}
        ) as resp:
            events = self._events(resp)
        assert "research" in events
        assert "sources" in events
        assert "token" in events
        assert "done" in events


class TestConversations:
    def test_crud(self, client):
        r = client.get("/api/conversations")
        assert r.status_code == 200
        assert r.json()["conversations"] == []

        r = client.post("/api/conversations", json={"title": "Test chat"})
        assert r.status_code == 200
        conv_id = r.json()["id"]

        r = client.get(f"/api/conversations/{conv_id}")
        assert r.status_code == 200
        assert r.json()["conversation"]["title"] == "Test chat"

        r = client.patch(f"/api/conversations/{conv_id}", json={"title": "Renamed"})
        assert r.status_code == 200
        assert client.get(f"/api/conversations/{conv_id}").json()["conversation"]["title"] == "Renamed"

        r = client.delete(f"/api/conversations/{conv_id}")
        assert r.status_code == 200
        assert client.get(f"/api/conversations/{conv_id}").status_code == 404


class TestRAG:
    def test_upload_and_list(self, client):
        resp = client.post(
            "/api/files/upload",
            files={"file": ("notes.txt", b"Nyvexa is an AI assistant with research tools.", "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["chunks"] >= 1

        listed = client.get("/api/files").json()
        assert len(listed["files"]) == 1

        fid = data["id"]
        assert client.delete(f"/api/files/{fid}").status_code == 200
        assert client.get("/api/files").json()["files"] == []

    def test_reject_bad_type(self, client):
        resp = client.post(
            "/api/files/upload",
            files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
        )
        assert resp.status_code == 400


class TestCalculatorTool:
    def test_math(self):
        from backend.services.tools import calculator

        result = calculator("2 * (3 + 4)")
        assert '"result": 14' in result

    def test_rejects_names(self):
        from backend.services.tools import calculator

        assert "error" in calculator("__import__('os')")


class TestAuth:
    def test_requires_login(self, client):
        # No token → 401 on protected routes
        r = client.get("/api/conversations", headers={"Authorization": ""})
        assert r.status_code == 401

    def test_signup_login_flow(self, client):
        # status is public
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        assert r.json()["auth_required"] is True

        # logout the fixture token (client still has its header, use a fresh client state)
        r = client.post(
            "/api/auth/signup",
            json={"email": "new@example.com", "password": "longenough1", "name": "New"},
            headers={"Authorization": ""},
        )
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "new@example.com"

        # duplicate signup rejected
        r = client.post(
            "/api/auth/signup",
            json={"email": "new@example.com", "password": "longenough1"},
            headers={"Authorization": ""},
        )
        assert r.status_code == 409

        # bad password rejected
        r = client.post(
            "/api/auth/login",
            json={"email": "new@example.com", "password": "wrongpass1"},
            headers={"Authorization": ""},
        )
        assert r.status_code == 401

        # good login
        r = client.post(
            "/api/auth/login",
            json={"email": "new@example.com", "password": "longenough1"},
            headers={"Authorization": ""},
        )
        assert r.status_code == 200
        login_token = r.json()["token"]

        # me works with new token
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login_token}"})
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "new@example.com"

        # conversations are user-scoped: fixture token must not see new user's convs
        client.post(
            "/api/conversations",
            json={"title": "for new user"},
            headers={"Authorization": f"Bearer {login_token}"},
        )
        r = client.get("/api/conversations")  # fixture token (tester@example.com)
        assert all(c["title"] != "for new user" for c in r.json()["conversations"])

        # logout revokes token
        r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {login_token}"})
        assert r.status_code == 200
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login_token}"})
        assert r.status_code == 401
