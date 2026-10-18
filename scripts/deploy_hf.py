#!/usr/bin/env python3
"""Create or refresh the Nyvexa Hugging Face ZeroGPU Space (free tier).

Usage:
    export HF_TOKEN=hf_xxx        # write-scoped user token
    .venv/bin/python scripts/deploy_hf.py

Requirements (HF rules for free ZeroGPU hosting):
  * verified email + account older than 30 days (max 2 ZeroGPU Spaces)
  * this app never uses the GPU, so the ZeroGPU quota stays untouched

Idempotent: re-runs re-upload code and re-apply secrets/variables, but never
overwrites an already-seeded dataset (production data wins).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPACE_NAME = os.getenv("NYVEXA_HF_SPACE", "nyvexa")
DATASET_NAME = os.getenv("NYVEXA_HF_DATASET", "nyvexa-data")
HARDWARE = "zero-a10g"
REMOTE_DB = "nyvexa.db.gz"
SENSITIVE_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")

SPACE_README = """\
---
title: Nyvexa
emoji: \U0001f30c
colorFrom: purple
colorTo: blue
sdk: gradio
sdk_version: 6.30.0
python_version: 3.12.12
app_file: app.py
license: mit
pinned: false
---

# Nyvexa

Full-stack AI assistant: SSE streaming chat, autonomous web research,
per-user memory and knowledge base.

FastAPI backend + SQLite; the database is restored from a private dataset on
every boot and pushed back periodically (the Space disk is ephemeral).
"""


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip("'\"")
        if val:
            out[key.strip()] = val
    return out


def resolve_token(arg: str | None) -> str:
    token = (arg or os.getenv("NYVEXA_HF_TOKEN") or os.getenv("HF_TOKEN") or "").strip()
    if not token:
        die(
            "no HF token. Create a write token at https://huggingface.co/settings/tokens "
            "and pass it via --token or the HF_TOKEN env var."
        )
    return token


def ignore_junk(_dir: str, names: list[str]) -> set[str]:
    skip = {"__pycache__", ".pytest_cache", ".ruff_cache", "tests", ".env", ".DS_Store"}
    return {n for n in names if n in skip or n.endswith((".pyc", ".pyo", ".db", ".gz"))}


def stage(dest: Path) -> None:
    shutil.copy2(ROOT / "app.py", dest / "app.py")
    # HF mounts only the root requirements.txt at /tmp, so a `-r` include
    # would break — emit a flat file from the real source of truth instead.
    (dest / "requirements.txt").write_text(
        (ROOT / "backend" / "requirements.txt").read_text()
    )
    (dest / "README.md").write_text(SPACE_README)
    shutil.copytree(ROOT / "backend", dest / "backend", ignore=ignore_junk)
    shutil.copytree(ROOT / "frontend", dest / "frontend", ignore=ignore_junk)


def verify_health(base_url: str, tries: int = 36, delay: float = 5.0) -> bool:
    import httpx

    last = ""
    for _ in range(tries):
        try:
            resp = httpx.get(
                f"{base_url}/api/health", timeout=20, follow_redirects=True
            )
            if resp.status_code == 200:
                print(f"health OK: {resp.text[:300]}")
                return True
            last = f"HTTP {resp.status_code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(delay)
    print(f"WARNING: health check never succeeded ({last})")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--token", help="HF write token (else HF_TOKEN env)")
    ap.add_argument("--space", default=SPACE_NAME, help="space name")
    ap.add_argument("--dataset", default=DATASET_NAME, help="dataset name")
    ap.add_argument("--no-wait", action="store_true", help="skip build/health wait")
    args = ap.parse_args()

    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    token = resolve_token(args.token)
    api = HfApi(token=token)
    user = api.whoami()["name"]
    space_id = f"{user}/{args.space}"
    dataset_id = f"{user}/{args.dataset}"
    print(f"account:   {user}")
    print(f"space:     {space_id}")
    print(f"dataset:   {dataset_id}")

    # 1. private dataset for DB persistence (Space disk is ephemeral)
    api.create_repo(dataset_id, repo_type="dataset", private=True, exist_ok=True)

    # 2. seed dataset from the local DB only if it has no snapshot yet
    if REMOTE_DB not in api.list_repo_files(dataset_id, repo_type="dataset"):
        local_db = ROOT / "data" / "nyvexa.db"
        if local_db.exists():
            os.environ["HF_DATASET_ID"] = dataset_id
            os.environ["NYVEXA_HF_TOKEN"] = token
            from backend.services import hf_sync

            gz = hf_sync.snapshot_gz()
            try:
                api.upload_file(
                    path_or_fileobj=str(gz),
                    path_in_repo=REMOTE_DB,
                    repo_id=dataset_id,
                    repo_type="dataset",
                    commit_message="seed initial nyvexa db",
                )
            finally:
                gz.unlink(missing_ok=True)
            print(f"seeded dataset with {local_db.stat().st_size} byte local DB")
        else:
            print("no local data/nyvexa.db — dataset starts empty")
    else:
        print("dataset already seeded — keeping existing data")

    # 3. public Gradio-SDK space on ZeroGPU hardware (free: no GPU usage)
    try:
        api.create_repo(
            space_id,
            repo_type="space",
            space_sdk="gradio",
            space_hardware=HARDWARE,
            private=False,
            exist_ok=True,
        )
    except HfHubHTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (402, 403):
            die(
                f"HF rejected ZeroGPU space creation (HTTP {status}).\n"
                "Free ZeroGPU hosting requires: verified email + account older than "
                "30 days (max 2 Spaces). Check https://huggingface.co/settings"
            )
        raise

    info = api.space_info(space_id)
    subdomain = getattr(info, "subdomain", None) or space_id.replace("/", "-")
    base_url = f"https://{subdomain}.hf.space"
    print(f"url:       {base_url}")

    # 4. secrets + variables BEFORE code upload (first build sees them)
    env = load_env(ROOT / "backend" / ".env")
    for key, value in env.items():
        if any(marker in key.upper() for marker in SENSITIVE_MARKERS):
            api.add_space_secret(space_id, key, value)
        else:
            api.add_space_variable(space_id, key, value)
    api.add_space_secret(space_id, "HF_DATASET_ID", dataset_id)
    api.add_space_secret(space_id, "NYVEXA_HF_TOKEN", token)
    api.add_space_variable(space_id, "GOOGLE_REDIRECT_URI", f"{base_url}/api/auth/google/callback")
    api.add_space_variable(space_id, "NYVEXA_KEEPALIVE_URL", f"{base_url}/api/health")
    api.add_space_variable(space_id, "NYVEXA_HF_PUSH_INTERVAL", "600")
    print(f"applied {len(env)} env entries from backend/.env + 5 deploy settings")

    # 5. upload code (triggers the build)
    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        api.upload_folder(
            folder_path=tmp,
            repo_id=space_id,
            repo_type="space",
            commit_message="deploy nyvexa",
        )
    print("uploaded app.py, requirements.txt, README.md, backend/, frontend/")

    # 6. safety: never ship secrets/data
    leaked = [
        f
        for f in api.list_repo_files(space_id, repo_type="space")
        if f.endswith(".env") or f.endswith(".db") or f == "info"
    ]
    if leaked:
        die(f"refusing to continue, sensitive files on the Space: {leaked}")
    print("sensitive-file check passed")

    # 7. wait for build + first boot, then health-check through the public URL
    if not args.no_wait:
        try:
            runtime = api.wait_for_space(space_id, timeout=600, poll_interval=5)
            stage_name = getattr(runtime, "stage", runtime)
            err = getattr(runtime, "error", None)
            print(f"runtime: {stage_name}" + (f" error={err}" if err else ""))
        except Exception as exc:
            print(f"WARNING: wait_for_space gave up: {exc}")
        verify_health(base_url)

    print(
        "\nDONE\n"
        f"  Space:     {base_url}\n"
        f"  Dataset:   {dataset_id} (private)\n"
        "  Next: register this OAuth redirect URI in Google Cloud Console\n"
        f"    {base_url}/api/auth/google/callback\n"
        "  (APIs & Services -> Credentials -> your OAuth client -> Authorized "
        "redirect URIs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
