"""RAG: chunk documents, embed via OpenRouter (fallback: lexical scoring), retrieve."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from backend import db
from backend.core.config import get_settings
from backend.services.llm import create_client

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
TOP_K = 5

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    # Prefer paragraph boundaries
    paras = re.split(r"\n\s*\n", text)
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= size:
                buf = p
            else:
                # hard-split long paragraph with overlap
                start = 0
                while start < len(p):
                    end = min(start + size, len(p))
                    chunks.append(p[start:end])
                    if end >= len(p):
                        break
                    start = end - overlap
                buf = ""
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c.strip()) > 40]


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _lexical_score(query: str, doc: str) -> float:
    qt = Counter(_tokens(query))
    dt = Counter(_tokens(doc))
    if not qt or not dt:
        return 0.0
    score = 0.0
    for term, qf in qt.items():
        df = dt.get(term, 0)
        if df:
            # simple tf weighting
            score += qf * (1 + math.log(df))
    return score / (len(qt) * (1 + math.log(1 + sum(dt.values())) / 5))


async def _embed(texts: list[str]) -> list[list[float]] | None:
    settings = get_settings()
    if not settings.configured:
        return None
    try:
        client = create_client(settings)
        resp = await client.embeddings.create(
            model="openai/text-embedding-3-small",
            input=texts[:64],
        )
        return [d.embedding for d in resp.data]
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


def ingest_document(name: str, raw_text: str, user_id: str) -> dict[str, Any]:
    chunks = chunk_text(raw_text)
    return db.add_rag_file(user_id, name, chunks)


def ingest_bytes(name: str, data: bytes, user_id: str) -> dict[str, Any]:
    """Ingest TXT/MD directly; PDF via pypdf when available."""
    lower = name.lower()
    if lower.endswith(".pdf"):
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise ValueError(f"Could not read PDF: {exc}") from exc
    else:
        text = data.decode("utf-8", errors="replace")
    return ingest_document(name, text, user_id)


async def retrieve(query: str, top_k: int = TOP_K, user_id: str | None = None) -> list[dict[str, Any]]:
    """Retrieve top-k chunks for a query. Embedding search with lexical fallback."""
    from backend.core.context import get_user_id

    uid = user_id or get_user_id()
    if not uid:
        return []
    chunks = db.all_chunks(uid)
    if not chunks:
        return []

    texts = [c["text"] for c in chunks]

    # Try embeddings for corpus (cap for cost) then query
    embeddings = None
    if len(texts) <= 256:
        embeddings = await _embed(texts)

    if embeddings:
        q_emb = await _embed([query])
        if q_emb:
            scored = sorted(
                zip(chunks, embeddings, strict=False),
                key=lambda pair: _cosine(q_emb[0], pair[1]),
                reverse=True,
            )
            return [
                {
                    "file": c["name"],
                    "chunk_index": c["idx"],
                    "text": c["text"][:1500],
                    "score": round(_cosine(q_emb[0], emb), 4),
                }
                for c, emb in scored[:top_k]
            ]

    # Lexical fallback
    scored = sorted(
        chunks,
        key=lambda c: _lexical_score(query, c["text"]),
        reverse=True,
    )
    results = []
    for c in scored[:top_k]:
        s = _lexical_score(query, c["text"])
        if s <= 0:
            continue
        results.append(
            {
                "file": c["name"],
                "chunk_index": c["idx"],
                "text": c["text"][:1500],
                "score": round(s, 4),
            }
        )
    return results
