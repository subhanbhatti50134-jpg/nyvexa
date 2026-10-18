"""Hugging Face Space entrypoint (sdk: gradio, app_file: app.py).

The HF builder executes this file as a plain script and waits for the app on
port 7860 — the same pattern as the official ``fastapi-uvicorn`` template.
Local development does not use this file (run ``uvicorn backend.main:app``).
"""

from __future__ import annotations

import logging
import threading
import time

import uvicorn

from backend.main import app

log = logging.getLogger("nyvexa.space")

try:  # preinstalled only on HF ZeroGPU spaces
    import spaces
    from spaces.zero import client as zerogpu_client

    @spaces.GPU
    def _nyvexa_gpu_probe() -> str:
        """ZeroGPU runtimes refuse to boot unless a @spaces.GPU fn exists."""
        return "nyvexa performs no local GPU inference"

    def _report_zerogpu() -> bool:
        for attempt in range(3):
            try:
                zerogpu_client.startup_report()
                log.info("zerogpu startup report sent (attempt %d)", attempt + 1)
                return True
            except Exception as exc:
                log.warning(
                    "zerogpu startup report attempt %d failed: %s", attempt + 1, exc
                )
                time.sleep(1)
        return False

    # ZeroGPU hosts wait for spaces' /startup-report during boot — normally
    # fired by gradio.one_launch, which a plain FastAPI app never triggers.
    # The host checks almost immediately after the app listens (it once killed
    # this boot <3s in), so report synchronously before uvicorn binds; a
    # delayed thread covers a daemon that wasn't reachable yet.
    if not _report_zerogpu():

        def _report_zerogpu_late() -> None:
            time.sleep(3)
            _report_zerogpu()

        threading.Thread(target=_report_zerogpu_late, daemon=True).start()

except ImportError:  # local dev / docker
    pass

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=7860,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )
