"""SQLite persistence for Nyvexa (stdlib only, thread-safe, user-scoped)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("NYVEXA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "nyvexa.db"

_PBKDF2_ITERATIONS = 200_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT DEFAULT '',
    password_hash TEXT,
    google_sub TEXT UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New chat',
    model TEXT,
    mode TEXT DEFAULT 'chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT DEFAULT 'chat',
    meta_json TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, created_at);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);
CREATE TABLE IF NOT EXISTS rag_files (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_user ON rag_files(user_id);
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES rag_files(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON rag_chunks(file_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_LEGACY_USER_ID = "legacy-local-user"
TOKEN_TTL_SECONDS = 30 * 24 * 3600  # sessions expire after 30 days

# Columns added after the original (pre-auth) schema. Used to upgrade old DBs in place.
_MIGRATION_COLUMNS: dict[str, dict[str, str]] = {
    "conversations": {
        "user_id": "TEXT",
        "model": "TEXT",
        "mode": "TEXT DEFAULT 'chat'",
    },
    "messages": {
        "mode": "TEXT DEFAULT 'chat'",
        "meta_json": "TEXT DEFAULT '{}'",
    },
    "auth_tokens": {"user_id": "TEXT", "expires_at": "REAL"},
    "rag_files": {"user_id": "TEXT"},
}

_USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT DEFAULT '',
    password_hash TEXT,
    google_sub TEXT UNIQUE,
    created_at REAL NOT NULL
);
"""


def _migrate_legacy(db: sqlite3.Connection) -> None:
    """Upgrade a pre-auth database: add user_id columns, create a legacy owner."""
    db.executescript(_USERS_DDL)

    for table, cols in _MIGRATION_COLUMNS.items():
        try:
            existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if not existing:
            continue  # table not created yet — full schema will create it
        for col, decl in cols.items():
            if col not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # Anonymous pre-auth tokens: give them an expiry, then drop the ownerless ones.
    try:
        db.execute(
            "UPDATE auth_tokens SET expires_at = created_at + ? WHERE expires_at IS NULL",
            (TOKEN_TTL_SECONDS,),
        )
        db.execute("DELETE FROM auth_tokens WHERE user_id IS NULL OR user_id=''")
    except sqlite3.Error:
        pass

    # Only materialize the legacy owner when pre-auth rows actually need one —
    # otherwise every restart would resurrect an empty orphan account.
    orphan = None
    for table in ("conversations", "rag_files"):
        try:
            orphan = db.execute(
                f"SELECT 1 FROM {table} WHERE user_id IS NULL OR user_id='' LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            continue
        if orphan:
            break
    if not orphan:
        return

    db.execute(
        "INSERT OR IGNORE INTO users (id, email, name, password_hash, google_sub, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (_LEGACY_USER_ID, "legacy@nyvexa.local", "Local user", None, None, time.time()),
    )
    # Pre-auth rows belong to the legacy owner and are claimed by the first
    # real account that signs up (see create_user).
    for table in ("conversations", "rag_files"):
        try:
            db.execute(
                f"UPDATE {table} SET user_id=? WHERE user_id IS NULL OR user_id=''",
                (_LEGACY_USER_ID,),
            )
        except sqlite3.Error:
            continue


def init_db() -> None:
    with get_db() as db:
        try:
            db.executescript(_SCHEMA)
            _migrate_legacy(db)  # tolerant no-op on fresh DBs; upgrades older columns
        except sqlite3.OperationalError:
            _migrate_legacy(db)
            db.executescript(_SCHEMA)
        try:
            db.execute(
                "DELETE FROM auth_tokens WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
        except sqlite3.Error:
            pass


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ---------- users ----------

def create_user(
    email: str,
    password: str | None = None,
    name: str = "",
    google_sub: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    user_id = _new_id()
    pw_hash = hash_password(password) if password else None
    with get_db() as db:
        # First real account inherits pre-auth (legacy) history.
        first_human = (
            db.execute("SELECT 1 FROM users WHERE id != ? LIMIT 1", (_LEGACY_USER_ID,)).fetchone()
            is None
        )
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, google_sub, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, email.strip().lower(), name.strip(), pw_hash, google_sub, now),
        )
        if first_human:
            db.execute(
                "UPDATE conversations SET user_id=? WHERE user_id=?", (user_id, _LEGACY_USER_ID)
            )
            db.execute(
                "UPDATE rag_files SET user_id=? WHERE user_id=?", (user_id, _LEGACY_USER_ID)
            )
            db.execute("DELETE FROM users WHERE id=?", (_LEGACY_USER_ID,))
    return {"id": user_id, "email": email.strip().lower(), "name": name.strip(), "created_at": now}


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_google_sub(sub: str) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def set_password(email: str, password: str) -> bool:
    with get_db() as db:
        cur = db.execute(
            "UPDATE users SET password_hash=? WHERE email=?",
            (hash_password(password), email.strip().lower()),
        )
        return cur.rowcount > 0


def link_google_sub(user_id: str, sub: str, name: str = "") -> None:
    with get_db() as db:
        if name:
            db.execute(
                "UPDATE users SET google_sub=?, name=COALESCE(NULLIF(name,''), ?) WHERE id=?",
                (sub, name, user_id),
            )
        else:
            db.execute("UPDATE users SET google_sub=? WHERE id=?", (sub, user_id))


# ---------- auth tokens ----------

def create_token(user_id: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with get_db() as db:
        db.execute(
            "INSERT INTO auth_tokens (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + TOKEN_TTL_SECONDS),
        )
    return token


def token_user(token: str) -> str | None:
    """Return user_id for a valid, unexpired token, or None."""
    if not token:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT user_id FROM auth_tokens WHERE token=? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (token, time.time()),
        ).fetchone()
        if row is None:
            return None
        return row["user_id"]


def token_valid(token: str) -> bool:
    if not token:
        return False
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM auth_tokens WHERE token=? AND (expires_at IS NULL OR expires_at > ?)",
            (token, time.time()),
        ).fetchone()
        return row is not None


def revoke_token(token: str) -> None:
    with get_db() as db:
        db.execute("DELETE FROM auth_tokens WHERE token=?", (token,))


# ---------- conversations ----------

def list_conversations(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, model, mode, created_at, updated_at FROM conversations "
            "WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def create_conversation(
    user_id: str, title: str = "New chat", model: str | None = None, mode: str = "chat"
) -> dict[str, Any]:
    now = time.time()
    conv_id = _new_id()
    with get_db() as db:
        db.execute(
            "INSERT INTO conversations (id, user_id, title, model, mode, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (conv_id, user_id, title[:120], model, mode, now, now),
        )
    return {
        "id": conv_id,
        "user_id": user_id,
        "title": title[:120],
        "model": model,
        "mode": mode,
        "created_at": now,
        "updated_at": now,
    }


def get_conversation(conv_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    with get_db() as db:
        if user_id:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id)
            ).fetchone()
        else:
            row = db.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return dict(row) if row else None


def touch_conversation(conv_id: str, title: str | None = None) -> None:
    with get_db() as db:
        if title is not None:
            db.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title[:120], time.time(), conv_id),
            )
        else:
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), conv_id))


def rename_conversation(conv_id: str, title: str, user_id: str) -> bool:
    with get_db() as db:
        cur = db.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=? AND user_id=?",
            (title[:120], time.time(), conv_id, user_id),
        )
        return cur.rowcount > 0


def delete_conversation(conv_id: str, user_id: str) -> bool:
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id)
        )
        return cur.rowcount > 0


# ---------- messages ----------

def add_message(
    conv_id: str,
    role: str,
    content: str,
    mode: str = "chat",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg_id = _new_id()
    now = time.time()
    with get_db() as db:
        db.execute(
            "INSERT INTO messages (id, conv_id, role, content, mode, meta_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (msg_id, conv_id, role, content, mode, json.dumps(meta or {}), now),
        )
        db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
    return {
        "id": msg_id,
        "conv_id": conv_id,
        "role": role,
        "content": content,
        "mode": mode,
        "meta": meta or {},
        "created_at": now,
    }


def get_messages(conv_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM messages WHERE conv_id=? ORDER BY created_at ASC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["meta"] = json.loads(d.pop("meta_json") or "{}")
            except json.JSONDecodeError:
                d["meta"] = {}
            out.append(d)
        return out


def delete_messages_after(conv_id: str, message_id: str) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT created_at FROM messages WHERE id=? AND conv_id=?",
            (message_id, conv_id),
        ).fetchone()
        if not row:
            return 0
        cur = db.execute(
            "DELETE FROM messages WHERE conv_id=? AND created_at >= ?",
            (conv_id, row["created_at"]),
        )
        return cur.rowcount


# ---------- RAG ----------

def add_rag_file(user_id: str, name: str, chunks: list[str]) -> dict[str, Any]:
    file_id = _new_id()
    now = time.time()
    with get_db() as db:
        db.execute(
            "INSERT INTO rag_files (id, user_id, name, created_at) VALUES (?,?,?,?)",
            (file_id, user_id, name[:200], now),
        )
        for i, text in enumerate(chunks):
            db.execute(
                "INSERT INTO rag_chunks (id, file_id, idx, text) VALUES (?,?,?,?)",
                (_new_id(), file_id, i, text),
            )
    return {"id": file_id, "name": name[:200], "chunks": len(chunks), "created_at": now}


def list_rag_files(user_id: str) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT f.id, f.name, f.created_at, COUNT(c.id) AS chunks "
            "FROM rag_files f LEFT JOIN rag_chunks c ON c.file_id=f.id "
            "WHERE f.user_id=? GROUP BY f.id ORDER BY f.created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_rag_file(file_id: str, user_id: str) -> bool:
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM rag_files WHERE id=? AND user_id=?", (file_id, user_id)
        )
        return cur.rowcount > 0


def all_chunks(user_id: str) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT c.id, c.file_id, c.idx, c.text, f.name FROM rag_chunks c "
            "JOIN rag_files f ON f.id=c.file_id WHERE f.user_id=?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
