"""
Runs the Telegram bot as a child process.

The dashboard keeps running when you restart the bot, which is the whole
point of editing source from the Umbrel UI: save file → Restart bot.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import IO

from config import LOG_DIR, SRC_DIR


class BotSupervisor:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._exit_code: int | None = None
        self._log_path = LOG_DIR / "bot.log"
        self._log_handle: IO[str] | None = None

    def status(self) -> dict:
        running = self._proc is not None and self._proc.poll() is None
        return {
            "running": running,
            "pid": self._proc.pid if running and self._proc else None,
            "started_at": self._started_at,
            "exit_code": None if running else self._exit_code,
            "log_path": str(self._log_path),
            "script": "thepiratebot.py",
        }

    def start(self) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.status()
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._log_handle = open(self._log_path, "a", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self._log_handle.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            self._log_handle.flush()
            self._proc = subprocess.Popen(
                ["python", "thepiratebot.py"],
                cwd=str(SRC_DIR),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            self._started_at = time.time()
            self._exit_code = None
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._exit_code = None if proc is None else proc.poll()
                return self.status()
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)
            self._exit_code = proc.poll()
            self._proc = None
            if self._log_handle:
                self._log_handle.write(f"--- stop {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                self._log_handle.flush()
        return self.status()

    def restart(self) -> dict:
        self.stop()
        return self.start()

    def tail_log(self, lines: int = 200) -> str:
        path = Path(self._log_path)
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        chunk = text.splitlines()[-lines:]
        return "\n".join(chunk)


supervisor = BotSupervisor()
