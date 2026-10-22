"""Regression tests for latency/accuracy paths: routing, history budget, research overlap."""

from __future__ import annotations

import asyncio
import time

import pytest

import backend.services.router as router_mod
from backend.services.llm import build_messages
from backend.services.research import _gather_sources
from backend.services.router import _heuristic, route_mode
from backend.services.websearch import Source


class _S:
    router_timeout = 3.0
    research_plan_timeout = 0.5
    research_max_queries = 3
    research_results_per_query = 2
    default_model = "m"
    max_history = 24
    history_char_budget = 30000


class TestHeuristicRouting:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("hello there", "chat"),
            ("explain how TLS handshakes work", "chat"),
            ("what is the latest stable Python version?", "research"),
            ("latest news about NVIDIA stock today", "research"),
            ("run a python script that visualizes primes", "agent"),
            ("fetch https://example.com and summarize it", "agent"),
            ("calculate 1759 * 393", "agent"),
            ("write a function that parses dates", "chat"),
            ("how do I run a marathon", "chat"),
            ("deep dive research into small language models", "deep"),
        ],
    )
    def test_clear_cases_never_call_llm(self, question, expected, monkeypatch):
        async def no_llm(*a, **k):
            raise AssertionError("LLM router must not be consulted for clear cases")

        monkeypatch.setattr(router_mod, "complete_json", no_llm)
        mode, reason = asyncio.run(route_mode(None, _S(), question))
        assert mode == expected
        assert "heuristic" in reason or reason == "auto"

    def test_ambiguous_falls_back_when_llm_fails(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(router_mod, "complete_json", boom)
        mode, _ = asyncio.run(route_mode(None, _S(), "deep dive research into the latest news"))
        assert mode in {"deep", "research"}  # deterministic priority: deep

    def test_ambiguous_uses_llm_when_healthy(self, monkeypatch):
        async def fake(*a, **k):
            return {"mode": "agent", "reason": "needs tools"}

        monkeypatch.setattr(router_mod, "complete_json", fake)
        mode, reason = asyncio.run(route_mode(None, _S(), "deep dive research into the latest news"))
        assert mode == "agent"
        assert reason == "needs tools"

    def test_ambiguous_llm_timeout_bounded(self, monkeypatch):
        async def slow(*a, **k):
            await asyncio.sleep(5)
            return {"mode": "chat", "reason": "late"}

        monkeypatch.setattr(router_mod, "complete_json", slow)
        t0 = time.perf_counter()
        mode, reason = asyncio.run(route_mode(None, _S(), "deep dive research into the latest news"))
        assert time.perf_counter() - t0 < 5  # budget, not the 5s sleep
        assert mode in {"deep", "research"}
        assert reason == "keyword heuristic"

    def test_heuristic_agent_precision(self):
        assert _heuristic("run a marathon trainer plan") == "chat"
        assert _heuristic("run python -m pytest") == "agent"


class TestHistoryBudget:
    def test_newest_turns_kept_oldest_dropped(self):
        history = [
            {"role": "user", "content": f"old-{i} " + "x" * 500} for i in range(50)
        ] + [
            {"role": "assistant", "content": "recent answer"},
            {"role": "user", "content": "newest question"},
        ]
        msgs = build_messages(history, "now", max_history=100, history_char_budget=1200)
        contents = [m["content"] for m in msgs if m["role"] != "system"]
        assert "newest question" in contents
        assert "recent answer" in contents
        assert not any(c.startswith("old-0 ") for c in contents)
        # Budget respected (system + final user message excluded from the check)
        history_total = sum(len(c) for c in contents[:-1])
        assert history_total <= 1200

    def test_no_budget_keeps_all_within_max_history(self):
        history = [{"role": "user", "content": f"m{i} " + "y" * 300} for i in range(30)]
        msgs = build_messages(history, "go", max_history=4, history_char_budget=None)
        contents = [m["content"] for m in msgs if m["role"] != "system"]
        assert len(contents) == 5  # 4 history + current
        assert contents[-2].startswith("m29")


class TestResearchOverlap:
    def test_planner_timeout_still_returns_sources(self, monkeypatch):
        import backend.services.research as research_mod

        async def slow_planner(*a, **k):
            await asyncio.sleep(3)
            return ["never happens"]

        def fake_search(queries, per_query=2):
            return [
                Source(title="t", url="https://example.com/a", snippet="s", query=q)
                for q in queries
            ]

        events: list[str] = []

        async def emit(event, data):
            events.append(event)

        monkeypatch.setattr(research_mod, "plan_queries", slow_planner)
        monkeypatch.setattr(research_mod, "search_sync", fake_search)

        t0 = time.perf_counter()
        sources, pages = asyncio.run(
            _gather_sources(object(), _S(), "what is the latest Python?", emit, fetch_full=False)
        )
        elapsed = time.perf_counter() - t0
        assert sources, "heuristic search must still run when planner is slow"
        assert pages == ""
        assert elapsed < 2.0  # bounded by plan_timeout(0.5s), not the 3s sleep
        assert "sources" in events

    def test_planned_queries_merge_and_dedupe(self, monkeypatch):
        import backend.services.research as research_mod

        async def fast_planner(*a, **k):
            return ["planned query"]

        seen_queries: list[list[str]] = []

        def fake_search(queries, per_query=2):
            seen_queries.append(list(queries))
            urls = {
                "what is the latest python?": "https://shared.example.com/x",
                "planned query": "https://shared.example.com/x",
            }
            return [
                Source(title="t", url=urls.get(q, f"https://example.com/{q}"), snippet="s", query=q)
                for q in queries
            ]

        async def emit(event, data):
            pass

        monkeypatch.setattr(research_mod, "plan_queries", fast_planner)
        monkeypatch.setattr(research_mod, "search_sync", fake_search)

        sources, _ = asyncio.run(
            _gather_sources(object(), _S(), "what is the latest python?", emit, fetch_full=False)
        )
        urls = [s.url for s in sources]
        assert urls.count("https://shared.example.com/x") == 1  # deduped
        assert any("planned query" in qs for qs in seen_queries)


class TestSearchSync:
    def test_parallel_search_dedupes_urls(self):
        from backend.services import websearch

        def fake_ddgs_text(query, max_results=4):
            return [
                {"href": "https://a.example/1", "title": "A", "body": "one"},
                {"href": "https://a.example/2", "title": "B", "body": "two"},
            ]

        class FakeDDGS:
            def __init__(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=4):
                return fake_ddgs_text(query, max_results)

        monkey = FakeDDGS()
        monkeypatch_targets = websearch._ddgs_class
        try:
            websearch._ddgs_class = lambda: (lambda: monkey)
            out = websearch.search_sync(["q1", "q2"], per_query=2)
        finally:
            websearch._ddgs_class = monkeypatch_targets
        assert len(out) == 2  # same URLs from both queries deduped
        assert {s.url for s in out} == {"https://a.example/1", "https://a.example/2"}


class _ChainSettings:
    nvidia_configured = True
    openrouter_configured = True
    default_model = "nvidia/a"
    fallback_model = "openrouter/b"

    @property
    def fallback_chain(self):
        return [self.fallback_model]

    def model_provider(self, model_id: str) -> str:
        return "nvidia" if model_id.startswith("nvidia/") else "openrouter"


def _delta_chunk(text: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None
    )


class _BoomStream:
    """Raises before yielding any token (like NIM's 16/16 worker rejection)."""

    def __aiter__(self):
        async def gen():
            raise RuntimeError("ResourceExhausted: Worker local total request limit reached (16/16)")
            yield  # pragma: no cover

        return gen()


class _OkStream:
    def __init__(self, tokens):
        self._tokens = tokens

    def __aiter__(self):
        async def gen():
            for t in self._tokens:
                yield _delta_chunk(t)

        return gen()


class _HalfFailStream:
    """Yields one token, then dies mid-stream."""

    def __aiter__(self):
        async def gen():
            yield _delta_chunk("partial ")
            raise RuntimeError("connection reset")

        return gen()


class TestMidstreamFallback:
    def _install_chain(self, monkeypatch, streams_by_model):
        import backend.services.llm as llm_mod

        class FakeClient:
            def __init__(self, model):
                self._model = model

            class chat:
                class completions:
                    @staticmethod
                    async def create(**kwargs):
                        model = kwargs["model"]
                        factory = streams_by_model.get(model)
                        if factory is None:
                            raise RuntimeError(f"no stream for {model}")
                        return factory()

        def fake_create_client(settings, provider=None, model=None):
            return FakeClient(model)

        monkeypatch.setattr(llm_mod, "create_client", fake_create_client)

    def test_failure_before_first_token_falls_back(self, monkeypatch):
        self._install_chain(
            monkeypatch,
            {"nvidia/a": _BoomStream, "openrouter/b": lambda: _OkStream(["Hello", " world"])},
        )
        from backend.services.research import _stream_with_usage

        async def collect():
            out = []
            async for kind, payload in _stream_with_usage(
                None, _ChainSettings(), [{"role": "user", "content": "hi"}], "nvidia/a"
            ):
                if kind == "token":
                    out.append(payload)
            return out

        tokens = asyncio.run(collect())
        assert tokens == ["Hello", " world"]

    def test_failure_after_first_token_propagates(self, monkeypatch):
        self._install_chain(
            monkeypatch,
            {
                "nvidia/a": lambda: _HalfFailStream(),
                "openrouter/b": lambda: _OkStream(["retry"]),
            },
        )
        from backend.services.research import _stream_with_usage

        async def collect():
            out = []
            async for kind, payload in _stream_with_usage(
                None, _ChainSettings(), [{"role": "user", "content": "hi"}], "nvidia/a"
            ):
                if kind == "token":
                    out.append(payload)
            return out

        with pytest.raises(RuntimeError, match="connection reset"):
            asyncio.run(collect())

    def test_all_candidates_failing_raises_last_error(self, monkeypatch):
        self._install_chain(
            monkeypatch, {"nvidia/a": _BoomStream, "openrouter/b": _BoomStream}
        )
        from backend.services.research import _stream_with_usage

        async def collect():
            async for _ in _stream_with_usage(
                None, _ChainSettings(), [{"role": "user", "content": "hi"}], "nvidia/a"
            ):
                pass

        with pytest.raises(RuntimeError, match="ResourceExhausted"):
            asyncio.run(collect())
