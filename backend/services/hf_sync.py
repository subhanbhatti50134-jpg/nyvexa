"""SQLite <-> Hugging Face dataset sync.

HF Spaces free tier has an EPHEMERAL disk: every rebuild/sleep-wake starts with an
empty /app/data. This module restores the DB from a private dataset at boot and
periodically pushes changes back (gzipped snapshot via the SQLite backup API, so
consistency is guaranteed even under concurrent writes).

Enabled only when BOTH env vars are set (they become Space secrets):
  HF_DATASET_ID   e.g. "username/nyvexa-data"
  NYVEXA_HF_TOKEN write-scoped user token (HF_TOKEN accepted as fallback;
                  HF may inject its own restricted HF_TOKEN, so prefer the
                  namespaced name)

No-op everywhere else (local dev, docker-compose, tests).
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from backend import db

log = logging.getLogger("nyvexa.hfsync")

DATASET_ID = os.getenv("HF_DATASET_ID", "").strip()
REMOTE_DB = "nyvexa.db.gz"
PUSH_INTERVAL = int(os.getenv("NYVEXA_HF_PUSH_INTERVAL", "90"))

_last_pushed_mtime = 0.0


def _token() -> str:
    return (os.getenv("NYVEXA_HF_TOKEN", "") or os.getenv("HF_TOKEN", "")).strip()


def enabled() -> bool:
    return bool(DATASET_ID and _token())


def _api():
    from huggingface_hub import HfApi

    return HfApi(token=_token() or None)


def _local_users() -> int:
    try:
        conn = sqlite3.connect(db.DB_PATH)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def snapshot_gz() -> Path:
    """Consistent gzip copy of the live DB (backup API = safe under writes)."""
    raw = db.DATA_DIR / "nyvexa.db.snapshot"
    gz = db.DATA_DIR / "nyvexa.db.snapshot.gz"
    raw.unlink(missing_ok=True)
    gz.unlink(missing_ok=True)
    src = sqlite3.connect(db.DB_PATH)
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with open(raw, "rb") as fin, gzip.open(gz, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    raw.unlink(missing_ok=True)
    return gz


def push_db(force: bool = False) -> bool:
    """Upload DB snapshot to the dataset if it changed. Returns True if pushed."""
    global _last_pushed_mtime
    if not enabled():
        return False
    try:
        mtime = db.DB_PATH.stat().st_mtime_ns
        if not force and mtime == _last_pushed_mtime:
            return False
        gz = snapshot_gz()
        size = gz.stat().st_size
        try:
            _api().upload_file(
                path_or_fileobj=str(gz),
                path_in_repo=REMOTE_DB,
                repo_id=DATASET_ID,
                repo_type="dataset",
                commit_message="nyvexa db snapshot",
            )
        finally:
            gz.unlink(missing_ok=True)
        _last_pushed_mtime = mtime
        log.info("pushed DB snapshot to %s (%d bytes)", DATASET_ID, size)
        return True
    except Exception as exc:
        log.warning("DB push failed: %s", exc)
        return False


def pull_db() -> bool:
    """Restore DB from dataset on fresh boots. Returns True if restored."""
    if not enabled():
        return False
    if _local_users() > 0:
        return False  # local DB already has real data — keep it
    try:
        from huggingface_hub import hf_hub_download

        cached = hf_hub_download(
            repo_id=DATASET_ID, filename=REMOTE_DB, repo_type="dataset", token=_token()
        )
    except Exception as exc:
        log.info("no remote DB to restore: %s", exc)
        return False
    try:
        with gzip.open(cached, "rb") as fin, open(db.DB_PATH, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        # integrity check before we trust it
        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        _last_pushed_mtime = db.DB_PATH.stat().st_mtime_ns
        log.info("restored DB from %s", DATASET_ID)
        return True
    except Exception as exc:
        log.warning("DB restore failed (keeping local): %s", exc)
        return False


async def sync_loop() -> None:
    """Background loop: push when the DB changes. Runs until cancelled."""
    if not enabled():
        return
    while True:
        await asyncio.sleep(PUSH_INTERVAL)
        try:
            await asyncio.to_thread(push_db)
        except Exception as exc:  # never let the loop die
            log.warning("sync tick failed: %s", exc)
