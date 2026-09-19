"""
FastAPI dashboard served through Umbrel's app proxy.

Tabs:
  Status   — start / stop / restart + live log tail
  Settings — token, language, currency
  Editor   — browse /data/src and save files (then restart the bot)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import PACKAGED_SRC, SRC_DIR, load_settings, save_settings
from supervisor import supervisor


WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = WEB_DIR / "templates"
STATIC = WEB_DIR / "static"

app = FastAPI(title="Telegram Botmanager", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# Anything that looks like source / config. Binaries stay out of the editor.
EDITABLE_SUFFIXES = {
    ".py", ".json", ".txt", ".md", ".sh", ".yml", ".yaml",
    ".css", ".js", ".html", ".svg", ".toml", ".ini", ".cfg",
    ".env", ".example",
}
SKIP_NAMES = {"__pycache__", ".git", ".venv", "dev-data"}


class SettingsPatch(BaseModel):
    bot_token: str | None = None
    bot_language: str | None = None
    delete_link_only_originals: bool | None = None
    vs_currency: str | None = None
    instagram_fix_host: str | None = None
    auto_start_bot: bool | None = None


class FileSave(BaseModel):
    path: str = Field(..., min_length=1)
    content: str


def _safe_src_path(relative: str) -> Path:
    """Resolve a path the UI asked for and reject anything outside /data/src."""
    rel = relative.lstrip("/")
    if ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="invalid path")
    target = (SRC_DIR / rel).resolve()
    src_root = SRC_DIR.resolve()
    if target != src_root and src_root not in target.parents:
        raise HTTPException(status_code=400, detail="path escapes source dir")
    return target


def _tree(root: Path, prefix: str = "") -> list[dict]:
    """Nested file tree for the editor sidebar."""
    entries: list[dict] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except FileNotFoundError:
        return entries
    for child in children:
        if child.name.startswith(".") or child.name in SKIP_NAMES:
            continue
        rel = str(Path(prefix) / child.name) if prefix else child.name
        if child.is_dir():
            entries.append({"name": child.name, "path": rel, "type": "dir", "children": _tree(child, rel)})
        elif child.suffix.lower() in EDITABLE_SUFFIXES or child.suffix == "":
            entries.append({"name": child.name, "path": rel, "type": "file"})
    return entries


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((TEMPLATES / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "bot": supervisor.status()}


@app.get("/api/status")
async def status() -> dict:
    settings = load_settings()
    safe = dict(settings)
    token = str(safe.get("bot_token") or "")
    safe["bot_token_set"] = bool(token)
    safe["bot_token_preview"] = (token[:6] + "…" + token[-4:]) if len(token) > 12 else ("set" if token else "")
    # Never send the raw token to the status endpoint (the settings GET does).
    safe.pop("bot_token", None)
    return {"bot": supervisor.status(), "settings": safe}


@app.post("/api/bot/start")
async def bot_start() -> dict:
    return supervisor.start()


@app.post("/api/bot/stop")
async def bot_stop() -> dict:
    return supervisor.stop()


@app.post("/api/bot/restart")
async def bot_restart() -> dict:
    return supervisor.restart()


@app.get("/api/logs")
async def logs(lines: int = 200) -> dict:
    return {"text": supervisor.tail_log(lines=min(max(lines, 20), 2000))}


@app.get("/api/settings")
async def get_settings() -> dict:
    return load_settings()


@app.post("/api/settings")
async def post_settings(patch: SettingsPatch) -> dict:
    payload = {k: v for k, v in patch.model_dump().items() if v is not None}
    saved = save_settings(payload)
    # Restart so language / token changes take effect immediately if the bot is up.
    if supervisor.status()["running"]:
        supervisor.restart()
    return saved


@app.get("/api/files")
async def list_files() -> dict:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    return {"root": str(SRC_DIR), "tree": _tree(SRC_DIR)}


@app.get("/api/files/read")
async def read_file(path: str) -> dict:
    target = _safe_src_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if target.stat().st_size > 1_000_000:
        raise HTTPException(status_code=400, detail="file too large")
    return {"path": path, "content": target.read_text(encoding="utf-8")}


@app.post("/api/files/save")
async def save_file(body: FileSave) -> dict:
    target = _safe_src_path(body.path)
    if target.suffix.lower() not in EDITABLE_SUFFIXES and target.suffix != "":
        raise HTTPException(status_code=400, detail="file type not editable")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": body.path, "bytes": target.stat().st_size}


@app.post("/api/files/reset")
async def reset_source() -> dict:
    """Replace /data/src with the packaged copy from the community-store app folder."""
    if not PACKAGED_SRC.exists():
        raise HTTPException(status_code=500, detail="packaged source missing")
    # Keep settings.json — only source is reset.
    if SRC_DIR.exists():
        for child in SRC_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    shutil.copytree(PACKAGED_SRC, SRC_DIR, dirs_exist_ok=True)
    return {"ok": True}


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    icon = WEB_DIR.parent.parent / "icon.svg"
    if icon.exists():
        return FileResponse(icon)
    raise HTTPException(status_code=404)
