"""Agent tools: web_search, fetch_page, calculator, run_python, rag_search."""

from __future__ import annotations

import ast
import asyncio
import json
import operator
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.core.config import get_settings
from backend.services import rag as rag_service
from backend.services.fetcher import fetch_page
from backend.services.websearch import search_sync

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

MAX_TOOL_ROUNDS = 5
PY_TIMEOUT = 8.0

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns JSON list of results with title, url, snippet.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Fetch a URL and return its readable text content (truncated).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Absolute http(s) URL"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a basic arithmetic expression. Supports + - * / // % ** and parentheses."
            ),
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "e.g. 2 * (3 + 4)"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Run a short Python snippet in a restricted subprocess. Prints go to stdout. 8s timeout."
            ),
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python 3 source code"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": (
                "Search the user's uploaded documents (knowledge base). Returns relevant excerpts."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("Unsupported expression")


def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body)
        return json.dumps({"result": result})
    except Exception as exc:
        return json.dumps({"error": f"Invalid expression: {exc}"})


_FORBIDDEN = (
    "import os", "import sys", "import subprocess", "import socket",
    "import shutil", "__import__", "open(", "exec(", "eval(", "breakpoint(",
)

# AST-level controls: substring deny-lists are trivially bypassed
# ("import  os", "from os import ...", "importlib.import_module('os')"),
# so imports must come from an allowlist and dangerous names/attrs are banned.
_ALLOWED_MODULES = frozenset({
    "math", "cmath", "random", "statistics", "itertools", "functools",
    "collections", "re", "json", "string", "datetime", "time", "calendar",
    "heapq", "bisect", "decimal", "fractions", "operator", "copy",
    "textwrap", "enum", "typing", "dataclasses", "array", "struct",
    "unicodedata", "difflib", "pprint", "uuid", "hashlib", "hmac", "base64",
    "zlib", "csv",
})
_BANNED_CALLS = frozenset({
    "exec", "eval", "compile", "__import__", "open", "breakpoint", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "memoryview", "exit", "quit",
})


def _ast_reject(tree: ast.AST) -> str | None:
    """Return a reason string when the AST uses anything outside the sandbox."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_MODULES:
                    return f"import not allowed: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return "relative imports are not allowed"
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_MODULES:
                return f"import not allowed: {node.module}"
        elif isinstance(node, ast.Name):
            if node.id in _BANNED_CALLS:
                return f"blocked name: {node.id}"
            if node.id.startswith("__") and node.id.endswith("__"):
                return f"blocked name: {node.id}"
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr == "open":
                return "blocked attribute: open"
            if attr.startswith("__") and attr.endswith("__"):
                return f"blocked attribute: {attr}"
    return None


def _sandbox_limits() -> None:
    """Run inside tight OS limits (best-effort; Linux only)."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except Exception:
        pass


def run_python(code: str) -> str:
    lowered = code.lower()
    for bad in _FORBIDDEN:
        if bad in lowered:
            return json.dumps({"error": f"Blocked pattern: {bad}"})
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc.msg} (line {exc.lineno})"})
    reason = _ast_reject(tree)
    if reason:
        return json.dumps({"error": f"Blocked: {reason}"})
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "snippet.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["python3", "-I", str(script)],
                capture_output=True,
                text=True,
                timeout=PY_TIMEOUT,
                cwd=tmp,
                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                preexec_fn=_sandbox_limits,
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Timeout after 8s"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {
                "stdout": proc.stdout[:6000],
                "stderr": proc.stderr[:2000],
                "returncode": proc.returncode,
            }
        )


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Run a tool by name; always returns a JSON string."""
    try:
        if name == "web_search":
            query = (arguments.get("query") or "").strip()
            settings = get_settings()
            sources = await asyncio.to_thread(
                search_sync, [query], settings.research_results_per_query
            )
            results = [
                {"title": s.title, "url": s.url, "snippet": s.snippet[:400]}
                for s in sources[:6]
            ]
            return json.dumps({"results": results})

        if name == "fetch_page":
            url = (arguments.get("url") or "").strip()
            page = await fetch_page(url)
            if not page:
                return json.dumps({"error": f"Could not fetch {url}"})
            return json.dumps({"title": page.title, "url": page.url, "content": page.text[:6000]})

        if name == "calculator":
            return calculator(arguments.get("expression") or "0")

        if name == "run_python":
            return run_python(arguments.get("code") or "pass")

        if name == "rag_search":
            query = (arguments.get("query") or "").strip()
            hits = await rag_service.retrieve(query, top_k=4)
            return json.dumps({"excerpts": hits})

        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        return json.dumps({"error": f"Tool failed: {exc}"})
