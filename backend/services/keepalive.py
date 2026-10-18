"""Keep a free Hugging Face Space awake.

Free Spaces suspend after ~48h without HTTP traffic. When
NYVEXA_KEEPALIVE_URL points at this app's public /api/health, a background
task GETs it every NYVEXA_KEEPALIVE_INTERVAL seconds so the Space never
sleeps while the process is alive. Failures are ignored (a sleeping Space
still counts the ping as traffic and wakes up on the next tick).
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

log = logging.getLogger("nyvexa.keepalive")

URL = os.getenv("NYVEXA_KEEPALIVE_URL", "").strip()
INTERVAL = int(os.getenv("NYVEXA_KEEPALIVE_INTERVAL", "900"))


def enabled() -> bool:
    return bool(URL)


async def loop() -> None:
    if not enabled():
        return
    log.info("keepalive pinging %s every %ds", URL, INTERVAL)
    while True:
        await asyncio.sleep(INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(URL)
            log.info("keepalive %s -> %s", URL, resp.status_code)
        except Exception as exc:
            log.warning("keepalive ping failed: %s", exc)
