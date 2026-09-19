"""
Persistent settings for the dashboard and the Telegram bot.

The live file lives at $FXBOT_DATA_DIR/config/settings.json so Umbrel
app updates do not wipe the BotFather token or language choice.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# Root of the persistent volume inside the container.
DATA_DIR = Path(os.environ.get("FXBOT_DATA_DIR", "/data"))
PACKAGED_SRC = Path(os.environ.get("FXBOT_PACKAGED_SRC", "/opt/default-src"))
SRC_DIR = DATA_DIR / "src"
CONFIG_DIR = DATA_DIR / "config"
LOG_DIR = DATA_DIR / "logs"
SETTINGS_PATH = CONFIG_DIR / "settings.json"

# Packaged defaults shipped next to this file (used only when no settings exist yet).
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults" / "config.json"

# Fields the UI is allowed to write. Anything else is ignored (keeps the file tidy).
ALLOWED_KEYS = {
    "bot_token",
    "bot_language",
    "delete_link_only_originals",
    "vs_currency",
    "instagram_fix_host",
    "allowed_chat_ids",
    "auto_start_bot",
}


def _defaults() -> dict[str, Any]:
    """Load factory defaults, then overlay TELEGRAM_BOT_TOKEN if Umbrel set it."""
    data: dict[str, Any] = {
        "bot_token": "",
        "bot_language": "en",
        "delete_link_only_originals": True,
        "vs_currency": "usd",
        "instagram_fix_host": "kirkstagram.com",
        "allowed_chat_ids": [],
        "auto_start_bot": True,
    }
    if _PACKAGED_DEFAULTS.exists():
        try:
            data.update(json.loads(_PACKAGED_DEFAULTS.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env_token and not data.get("bot_token"):
        data["bot_token"] = env_token
    return data


def ensure_dirs() -> None:
    """Create the folders the rest of the app expects."""
    for path in (DATA_DIR, SRC_DIR, CONFIG_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    """Return settings merged on top of defaults (so old files stay valid)."""
    ensure_dirs()
    merged = _defaults()
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                merged.update(saved)
        except json.JSONDecodeError:
            pass
    return merged


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial update from the UI and write it to disk."""
    current = load_settings()
    for key, value in patch.items():
        if key not in ALLOWED_KEYS:
            continue
        current[key] = value
    # Normalise language + currency so the bot never sees junk values.
    lang = str(current.get("bot_language", "en")).lower()
    current["bot_language"] = "de" if lang.startswith("de") else "en"
    current["vs_currency"] = str(current.get("vs_currency") or "usd").lower()
    current["bot_token"] = str(current.get("bot_token") or "").strip()
    host = str(current.get("instagram_fix_host") or "kirkstagram.com")
    host = host.replace("https://", "").replace("http://", "").strip("/")
    current["instagram_fix_host"] = host or "kirkstagram.com"
    SETTINGS_PATH.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current
