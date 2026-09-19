"""
Process that Umbrel actually keeps running.

It serves the dashboard and, when settings.auto_start_bot is true and a
token is present, also launches the Telegram bot as a child process.
"""

from __future__ import annotations

import os

import uvicorn

from config import ensure_dirs, load_settings
from supervisor import supervisor
from web.server import app


def main() -> None:
    ensure_dirs()
    settings = load_settings()
    if settings.get("auto_start_bot") and settings.get("bot_token"):
        supervisor.start()

    host = os.environ.get("FXBOT_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("FXBOT_WEB_PORT", "8080"))
    # reload=False: the editor writes files that should not bounce the UI.
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
