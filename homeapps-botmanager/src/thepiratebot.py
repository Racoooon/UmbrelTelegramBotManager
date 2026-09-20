#!/usr/bin/env python3
"""
ThePirateBot — the entire Telegram bot in this one file.

Edit this file in the Umbrel app (Telegram Botmanager → Source editor),
save, then click Restart. The dashboard process stays up; only this
process is recycled.

What it does
------------
* X / Twitter URLs  -> fixupx.com     (View Tweet button)
* Instagram URLs    -> kirkstagram.com by default (View Post button)
  Host is configurable in the dashboard (ddinstagram, instagramez, …).
* Delete button works only for the person who posted the original.
* Original Telegram message is deleted only when it contains nothing
  except rewriteable links (bot needs group admin + delete permission).
* Crypto: /p /c /ath /trending /dom via CoinGecko (no API key).

Settings are read from $FXBOT_DATA_DIR/config/settings.json
(the same file the dashboard writes).
"""

from __future__ import annotations

import io
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import matplotlib

matplotlib.use("Agg")  # no display inside Docker
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from telegram import (  # noqa: E402
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import (  # noqa: E402
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ---------------------------------------------------------------------------
# Paths / settings (same contract as config.py in the dashboard)
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("FXBOT_DATA_DIR", "/data"))
SETTINGS_PATH = DATA_DIR / "config" / "settings.json"

log = logging.getLogger("thepiratebot")


def load_settings() -> dict[str, Any]:
    """Read dashboard settings; fall back to safe defaults if the file is missing."""
    defaults: dict[str, Any] = {
        "bot_token": "",
        "bot_language": "en",
        "delete_link_only_originals": True,
        "vs_currency": "usd",
        "instagram_fix_host": "kirkstagram.com",
        "auto_start_bot": True,
        "xai_api_key": "",
    }
    if SETTINGS_PATH.exists():
        try:
            import json

            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                defaults.update(saved)
        except Exception as exc:  # noqa: BLE001 — keep the bot alive on a bad file
            log.warning("Could not read settings.json: %s", exc)
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env_token and not defaults.get("bot_token"):
        defaults["bot_token"] = env_token
    return defaults


# ---------------------------------------------------------------------------
# English / German strings — dashboard toggle writes bot_language: en|de
# ---------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "start": (
            "Ahoy {name}. I am ThePirateBot.\n"
            "I rewrite X and Instagram links so Telegram can embed them, "
            "and I answer crypto commands.\n\nTry /help"
        ),
        "help": (
            "<b>ThePirateBot</b>\n\n"
            "<b>Links</b>\n"
            "Post an x.com / twitter.com or instagram.com URL.\n"
            "I reply with a fixed embed link.\n"
            "Buttons: <i>Delete</i> (only you) · <i>View Tweet / View Post</i>\n"
            "If your message is only the link, I also delete the original "
            "(I need to be group admin with delete rights).\n\n"
            "<b>Crypto</b>\n"
            "/p btc — price, 1h/24h/7d/30d, ATH, cap, volume\n"
            "/p btc eur — same in EUR\n"
            "/c btc — chart (default 1h; buttons 15m 30m 1h 4h 1d)\n"
            "/ath btc — all-time high\n"
            "/trending — CoinGecko trending\n"
            "/dom — Bitcoin dominance\n"
            "/ask your question — Grok (needs xAI key in Settings)\n"
            "/help — this text"
        ),
        "need_symbol": "Please add a coin, e.g. <code>/p btc</code>",
        "unknown_coin": "I could not find a coin matching <code>{query}</code>.",
        "coingecko_error": "CoinGecko is busy or rate-limited. Try again in a minute.",
        "price_header": "<b>{name}</b> ({symbol})",
        "price_now": "Now: <b>{price}</b>",
        "price_changes": "1h {h1} · 24h {h24} · 7d {d7} · 30d {d30}",
        "price_ath": "ATH: {ath} ({ath_date})  {ath_change}",
        "price_mcap": "Market cap: {mcap}",
        "price_vol": "24h volume: {vol}",
        "price_rank": "Rank: #{rank}",
        "chart_caption": "{name} ({symbol}) · {days} · {vs}",
        "ath_line": "<b>{name}</b> ATH: {ath} on {ath_date}\nNow {price} ({ath_change} from ATH)",
        "trending_header": "<b>Trending on CoinGecko</b>",
        "dom_line": "Bitcoin dominance: <b>{btc}%</b>\nEthereum: {eth}%\nMarket cap: {mcap}",
        "btn_delete": "Delete",
        "btn_view_tweet": "View Tweet",
        "btn_view_post": "View Post",
        "deleted": "Deleted.",
        "not_owner": "Only the person who posted the original link can delete this.",
        "ask_need": "Usage: <code>/ask your question</code> or reply to a message with /ask.",
        "ask_no_key": "No xAI API key set. Open Telegram Botmanager → Settings and paste your key from console.x.ai.",
        "ask_error": "Grok request failed. Check the key and try again.",
        "btn_refresh": "Refresh",
        "btn_chart": "Chart",
    },
    "de": {
        "start": (
            "Ahoi {name}. Ich bin ThePirateBot.\n"
            "Ich mache aus X- und Instagram-Links einbettbare Links "
            "und beantworte Krypto-Befehle.\n\nTippe /help"
        ),
        "help": (
            "<b>ThePirateBot</b>\n\n"
            "<b>Links</b>\n"
            "Poste eine x.com- / twitter.com- oder instagram.com-URL.\n"
            "Ich antworte mit einem Fix-Link zum Einbetten.\n"
            "Buttons: <i>Löschen</i> (nur du) · <i>Tweet / Beitrag ansehen</i>\n"
            "Besteht die Nachricht nur aus dem Link, lösche ich das Original "
            "(dazu brauche ich Admin-Rechte).\n\n"
            "<b>Krypto</b>\n"
            "/p btc — Preis, 1h/24h/7d/30d, ATH, Marktkap., Volumen\n"
            "/p btc eur — dasselbe in EUR\n"
            "/c btc — Chart (Standard 1h; Buttons 15m 30m 1h 4h 1d)\n"
            "/ath btc — Allzeithoch\n"
            "/trending — Trend-Coins\n"
            "/dom — Bitcoin-Dominanz\n"
            "/ask deine Frage — Grok (xAI-Key in den Einstellungen)\n"
            "/help — dieser Text"
        ),
        "need_symbol": "Bitte eine Münze angeben, z. B. <code>/p btc</code>",
        "unknown_coin": "Keine Münze zu <code>{query}</code> gefunden.",
        "coingecko_error": "CoinGecko ist beschäftigt oder limitiert. In einer Minute nochmal versuchen.",
        "price_header": "<b>{name}</b> ({symbol})",
        "price_now": "Jetzt: <b>{price}</b>",
        "price_changes": "1h {h1} · 24h {h24} · 7d {d7} · 30d {d30}",
        "price_ath": "ATH: {ath} ({ath_date})  {ath_change}",
        "price_mcap": "Marktkap.: {mcap}",
        "price_vol": "24h-Volumen: {vol}",
        "price_rank": "Rang: #{rank}",
        "chart_caption": "{name} ({symbol}) · {days} · {vs}",
        "ath_line": "<b>{name}</b> ATH: {ath} am {ath_date}\nJetzt {price} ({ath_change} vom ATH)",
        "trending_header": "<b>Trending auf CoinGecko</b>",
        "dom_line": "Bitcoin-Dominanz: <b>{btc}%</b>\nEthereum: {eth}%\nMarktkapitalisierung: {mcap}",
        "btn_delete": "Löschen",
        "btn_view_tweet": "Tweet ansehen",
        "btn_view_post": "Beitrag ansehen",
        "deleted": "Gelöscht.",
        "not_owner": "Nur wer den Original-Link gepostet hat, kann das löschen.",
        "ask_need": "Nutzung: <code>/ask deine Frage</code> oder auf eine Nachricht mit /ask antworten.",
        "ask_no_key": "Kein xAI-API-Key. In Telegram Botmanager → Settings den Key von console.x.ai eintragen.",
        "ask_error": "Grok-Anfrage fehlgeschlagen. Key prüfen und nochmal versuchen.",
        "btn_refresh": "Aktualisieren",
        "btn_chart": "Chart",
    },
}


def t(lang: str, key: str, **kwargs: object) -> str:
    table = STRINGS.get(lang, STRINGS["en"])
    template = table.get(key) or STRINGS["en"].get(key) or key
    return template.format(**kwargs) if kwargs else template


def lang_of(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings = context.bot_data.get("settings") or {}
    raw = str(settings.get("bot_language") or "en").lower()
    return "de" if raw.startswith("de") else "en"


def vs_of(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings = context.bot_data.get("settings") or {}
    return str(settings.get("vs_currency") or "usd").lower()


# ---------------------------------------------------------------------------
# Link rewrite — X/Twitter and Instagram
# ---------------------------------------------------------------------------

# X / Twitter status-style URLs (www / mobile included).
X_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/[^\s<>()]+",
    re.IGNORECASE,
)

# Instagram posts, reels, TV, stories. Also instagr.am short links.
IG_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/[^\s<>()]+",
    re.IGNORECASE,
)

# Hosts we already consider "fixed" — do not rewrite twice.
IG_FIX_HOSTS = {
    "kirkstagram.com",
    "ddinstagram.com",
    "kkinstagram.com",
    "instagramez.com",
    "eeinstagram.com",
    "uuinstagram.com",
    "vxinstagram.com",
}


def _strip_trailing_punct(url: str) -> str:
    return url.strip().rstrip(".,;!?)]}")


STATUS_ID_RE = re.compile(r"/status/(\d+)", re.IGNORECASE)


def fix_x(url: str) -> tuple[str, str]:
    """Return (fxtwitter embed url, cleaned x.com url for View Tweet)."""
    parsed = urlparse(_strip_trailing_punct(url))
    view = urlunparse(("https", "x.com", parsed.path, "", "", ""))
    # fxtwitter matches Phanes-style cards; HQ media is fetched separately.
    fixed = urlunparse(("https", "fxtwitter.com", parsed.path, "", "", ""))
    return fixed, view


def tweet_id_from_url(url: str) -> str | None:
    match = STATUS_ID_RE.search(url or "")
    return match.group(1) if match else None


def _best_video_url(video: dict[str, Any]) -> str | None:
    variants = video.get("variants") or video.get("formats") or []
    best_url = video.get("url")
    best_br = -1
    for item in variants:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        bitrate = int(item.get("bitrate") or 0)
        container = str(item.get("content_type") or item.get("container") or "")
        if "m3u8" in url or "mpegURL" in container:
            continue
        if bitrate >= best_br:
            best_br = bitrate
            best_url = url
    return best_url


def orig_photo_url(url: str) -> str:
    if "pbs.twimg.com" in url and "name=" not in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}name=orig"
    return url.replace("name=small", "name=orig").replace("name=medium", "name=orig").replace("name=large", "name=orig")


async def fetch_fx_tweet(status_id: str) -> dict[str, Any] | None:
    urls = [
        f"https://api.fxtwitter.com/status/{status_id}",
        f"https://api.fxtwitter.com/2/status/{status_id}",
    ]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url in urls:
            try:
                response = await client.get(url, headers={"User-Agent": "ThePirateBot/1.0"})
                if response.status_code >= 400:
                    continue
                payload = response.json()
            except Exception:
                continue
            tweet = payload.get("tweet") or payload.get("status") or payload
            if isinstance(tweet, dict) and (tweet.get("id") or tweet.get("text")):
                return tweet
    return None


def extract_fx_media(tweet: dict[str, Any]) -> list[dict[str, str]]:
    media_block = tweet.get("media") or {}
    items: list[dict[str, str]] = []
    for video in media_block.get("videos") or []:
        url = _best_video_url(video) if isinstance(video, dict) else None
        if url:
            items.append({"type": "video", "url": url})
    for photo in media_block.get("photos") or []:
        url = photo.get("url") if isinstance(photo, dict) else None
        if url:
            items.append({"type": "photo", "url": orig_photo_url(url)})
    for extra in media_block.get("all") or []:
        if not isinstance(extra, dict):
            continue
        kind = extra.get("type")
        url = extra.get("url")
        if kind == "video":
            url = _best_video_url(extra) or url
            if url and not any(i["url"] == url for i in items):
                items.append({"type": "video", "url": url})
        elif url and not any(i["url"] == url for i in items):
            items.append({"type": "photo", "url": orig_photo_url(url)})
    return items[:10]


def fix_ig(url: str, fix_host: str) -> tuple[str, str]:
    """Return (fixed_embed_url, cleaned instagram.com url for View Post)."""
    parsed = urlparse(_strip_trailing_punct(url))
    host = (parsed.hostname or "").lower().lstrip("www.")
    path = parsed.path or "/"
    view = urlunparse(("https", "www.instagram.com", path, "", "", ""))
    if host in IG_FIX_HOSTS:
        return urlunparse(("https", host, path, "", "", "")), view
    host = (fix_host or "kirkstagram.com").strip().lower()
    host = host.replace("https://", "").replace("http://", "").strip("/")
    fixed = urlunparse(("https", host, path, "", "", ""))
    return fixed, view


def collect_links(text: str, ig_host: str) -> list[dict[str, str]]:
    """
    Find rewriteable URLs in display order.
    Each item: kind (x|ig), original, fixed, view.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in X_URL_RE.findall(text or ""):
        if raw in seen:
            continue
        seen.add(raw)
        fixed, view = fix_x(raw)
        found.append({"kind": "x", "original": raw, "fixed": fixed, "view": view})
    for raw in IG_URL_RE.findall(text or ""):
        if raw in seen:
            continue
        seen.add(raw)
        fixed, view = fix_ig(raw, ig_host)
        found.append({"kind": "ig", "original": raw, "fixed": fixed, "view": view})
    return found


def is_links_only(text: str, originals: list[str]) -> bool:
    leftover = text or ""
    for url in originals:
        leftover = leftover.replace(url, "")
    return leftover.strip() == ""


def link_keyboard(lang: str, poster_id: int, kind: str, view_url: str) -> InlineKeyboardMarkup:
    view_key = "btn_view_tweet" if kind == "x" else "btn_view_post"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "btn_delete"), callback_data=f"d:{poster_id}"),
                InlineKeyboardButton(t(lang, view_key), url=view_url),
            ]
        ]
    )


async def on_text_maybe_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text or message.text.startswith("/"):
        return

    settings = context.bot_data.get("settings") or {}
    ig_host = str(settings.get("instagram_fix_host") or "kirkstagram.com")
    items = collect_links(message.text, ig_host)
    if not items:
        return

    language = lang_of(context)
    poster_id = update.effective_user.id if update.effective_user else 0

    for item in items:
        markup = link_keyboard(language, poster_id, item["kind"], item["view"])
        if item["kind"] == "x":
            await reply_x_phanes(message, item, markup)
        else:
            await message.reply_text(
                item["fixed"],
                disable_web_page_preview=False,
                reply_markup=markup,
            )

    if settings.get("delete_link_only_originals", True) and is_links_only(
        message.text, [i["original"] for i in items]
    ):
        try:
            await message.delete()
        except Exception:
            pass


async def reply_x_phanes(message, item: dict[str, str], markup: InlineKeyboardMarkup) -> None:
    """Phanes layout: one fxtwitter link so Telegram draws the card. prefer_large_media = bigger preview."""
    kwargs: dict[str, Any] = {"reply_markup": markup, "disable_web_page_preview": False}
    try:
        from telegram import LinkPreviewOptions

        kwargs["link_preview_options"] = LinkPreviewOptions(
            is_disabled=False,
            prefer_large_media=True,
            show_above_text=True,
        )
        kwargs.pop("disable_web_page_preview", None)
    except Exception:
        pass
    await message.reply_text(item["fixed"], **kwargs)


async def on_delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data or not query.data.startswith("d:"):
        if query:
            await query.answer()
        return

    language = lang_of(context)
    try:
        owner_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.answer()
        return

    presser = query.from_user.id if query.from_user else 0
    if presser != owner_id:
        await query.answer(t(language, "not_owner"), show_alert=True)
        return

    await query.answer(t(language, "deleted"))
    if query.message:
        try:
            await query.message.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------

COINGECKO = "https://api.coingecko.com/api/v3"
ALIAS_TO_ID = {
    "btc": "bitcoin",
    "xbt": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "xrp": "ripple",
    "doge": "dogecoin",
    "ada": "cardano",
    "avax": "avalanche-2",
    "dot": "polkadot",
    "link": "chainlink",
    "ltc": "litecoin",
    "bch": "bitcoin-cash",
    "atom": "cosmos",
    "near": "near",
    "uni": "uniswap",
    "apt": "aptos",
    "sui": "sui",
    "ton": "the-open-network",
    "trx": "tron",
    "matic": "matic-network",
    "pol": "polygon-ecosystem-token",
    "shib": "shiba-inu",
    "pepe": "pepe",
    "wif": "dogwifcoin",
    "bnb": "binancecoin",
    "usdt": "tether",
    "usdc": "usd-coin",
}
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 45.0

TIMEFRAMES = {
    "1h": "1",
    "4h": "1",
    "1d": "1",
    "7d": "7",
    "1w": "7",
    "14d": "14",
    "1m": "30",
    "30d": "30",
    "90d": "90",
    "1y": "365",
}


class CoinGeckoError(Exception):
    pass


async def cg_get(path: str, params: dict[str, Any] | None = None) -> Any:
    key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{COINGECKO}{path}", params=params)
    if response.status_code == 429:
        raise CoinGeckoError("rate limited")
    if response.status_code >= 400:
        raise CoinGeckoError(f"http {response.status_code}")
    data = response.json()
    _CACHE[key] = (now + _CACHE_TTL, data)
    return data


async def resolve_coin(query: str) -> dict[str, str] | None:
    raw = (query or "").strip().lower()
    if not raw:
        return None
    if raw in ALIAS_TO_ID:
        coin_id = ALIAS_TO_ID[raw]
        return {"id": coin_id, "symbol": raw, "name": coin_id.replace("-", " ").title()}
    data = await cg_get("/search", {"query": raw})
    coins = data.get("coins") or []
    if not coins:
        return None
    exact = next((c for c in coins if str(c.get("symbol", "")).lower() == raw), None)
    chosen = exact or coins[0]
    return {
        "id": chosen.get("id") or raw,
        "symbol": str(chosen.get("symbol") or raw).lower(),
        "name": chosen.get("name") or raw,
    }


def money(value: float | int | None, vs: str) -> str:
    if value is None:
        return "—"
    number = float(value)
    sign = {"usd": "$", "eur": "€", "gbp": "£"}.get(vs.lower(), "")
    abs_number = abs(number)
    if abs_number >= 1_000_000_000_000:
        body = f"{number / 1_000_000_000_000:.2f}T"
    elif abs_number >= 1_000_000_000:
        body = f"{number / 1_000_000_000:.2f}B"
    elif abs_number >= 1_000_000:
        body = f"{number / 1_000_000:.2f}M"
    elif abs_number >= 1_000:
        body = f"{number:,.0f}"
    elif abs_number >= 1:
        body = f"{number:,.2f}"
    elif abs_number >= 0.01:
        body = f"{number:,.4f}"
    else:
        body = f"{number:.8f}".rstrip("0").rstrip(".")
    return f"{sign}{body}" if sign else f"{body} {vs.upper()}"


def pct(value: float | int | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{'▲' if number >= 0 else '▼'}{abs(number):.2f}%"


def day_label(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]


CHART_INTERVALS = ("15m", "30m", "1h", "4h", "1d")
BINANCE_INTERVAL = {"15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"}
BINANCE_LIMIT = {"15m": 192, "30m": 168, "1h": 120, "4h": 120, "1d": 90}


async def fetch_binance_klines(symbol: str, interval: str) -> list[list[float]]:
    pair = f"{symbol.upper()}USDT"
    params = {
        "symbol": pair,
        "interval": BINANCE_INTERVAL.get(interval, "1h"),
        "limit": BINANCE_LIMIT.get(interval, 72),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get("https://api.binance.com/api/v3/klines", params=params)
    if response.status_code >= 400:
        raise CoinGeckoError(f"binance {response.status_code}")
    rows = response.json()
    candles = []
    for row in rows:
        candles.append(
            [
                float(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            ]
        )
    if not candles:
        raise CoinGeckoError("no klines")
    return candles


def render_ohlc_png(
    candles: list[list[float]],
    title: str,
    vs: str,
    theme: str = "light",
) -> bytes:
    if not candles:
        raise ValueError("no candles")
    opens = [c[1] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    vols = [c[5] if len(c) > 5 else 0.0 for c in candles]
    xs = [mdates.date2num(datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc)) for c in candles]
    last = closes[-1]
    pmin, pmax = min(lows), max(highs)
    prange = max(pmax - pmin, 1e-9)
    pad = prange * 0.08
    # Paint volume into the bottom 18% of the price pane (CoinTrendz overlay).
    vmax = max(vols) or 1.0
    vol_floor = pmin - pad
    vol_ceil = pmin + prange * 0.18
    up, down = "#26a69a", "#ef5350"
    if theme == "dark":
        bg, text, muted, grid, accent = "#0b1220", "#e5e7eb", "#9ca3af", "#1f2937", "#f59e0b"
    else:
        bg, text, muted, grid, accent = "#ffffff", "#111827", "#6b7280", "#eceff3", "#f59e0b"

    fig, ax = plt.subplots(figsize=(12.2, 6.4), dpi=160)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(colors=muted, labelsize=8, length=0)
    ax.grid(True, color=grid, linestyle="-", linewidth=0.7)
    for spine in ax.spines.values():
        spine.set_color(grid)
    ax.spines["top"].set_visible(False)

    delta = xs[1] - xs[0] if len(xs) > 1 else 0.03
    width = max(delta * 0.62, 0.0003)
    for i, x in enumerate(xs):
        color = up if closes[i] >= opens[i] else down
        vol_h = (vols[i] / vmax) * (vol_ceil - vol_floor)
        ax.bar(x, vol_h, width=width * 1.05, bottom=vol_floor, color=color, alpha=0.22, align="center", zorder=1)
        ax.vlines(x, lows[i], highs[i], color=color, linewidth=1.05, zorder=2)
        body_low = min(opens[i], closes[i])
        body_h = max(abs(closes[i] - opens[i]), prange * 0.0012)
        ax.add_patch(
            Rectangle((x - width / 2, body_low), width, body_h, facecolor=color, edgecolor=color, zorder=3)
        )

    ax.set_ylim(vol_floor, pmax + pad * 1.6)
    ax.axhline(last, color=up, linestyle="--", linewidth=0.8, alpha=0.7, zorder=4)
    ax.annotate(
        f"{last:,.2f}",
        xy=(xs[-1], last),
        xytext=(10, 0),
        textcoords="offset points",
        color="#ffffff",
        fontsize=8,
        fontweight="bold",
        va="center",
        zorder=5,
        bbox=dict(boxstyle="round,pad=0.28", fc=up, ec="none"),
    )
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    last_chg = ((c - o) / o * 100) if o else 0.0
    fig.text(0.02, 0.965, title, color=accent, fontsize=11, fontweight="bold", ha="left", va="top")
    fig.text(
        0.02,
        0.925,
        f"O{o:.2f}  H{h:.2f}  L{l:.2f}  C{c:.2f}  ({last_chg:+.2f}%)",
        color=up if last_chg >= 0 else down,
        fontsize=9,
        ha="left",
        va="top",
    )
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
    ax.set_xlim(xs[0] - delta, xs[-1] + delta * 3)
    fig.subplots_adjust(left=0.03, right=0.93, top=0.88, bottom=0.08)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


async def _resolve_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    language = lang_of(context)
    message = update.effective_message
    if not query:
        await message.reply_html(t(language, "need_symbol"))
        return None
    try:
        coin = await resolve_coin(query)
    except CoinGeckoError:
        await message.reply_html(t(language, "coingecko_error"))
        return None
    if not coin:
        await message.reply_html(t(language, "unknown_coin", query=query))
        return None
    return coin


def _args(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return [a.strip() for a in (context.args or []) if a.strip()]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.effective_message.reply_text(t(lang_of(context), "start", name=name))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(t(lang_of(context), "help"))


def _change_row(label: str, value: float | None) -> str:
    if value is None:
        return f"{label}: —"
    number = float(value)
    arrow = "📈" if number >= 0 else "📉"
    return f"{arrow} {label}: {number:+.2f}%"


def format_phanes_price(detail: dict[str, Any], vs: str) -> str:
    md = detail.get("market_data") or {}
    name = detail.get("name") or "?"
    tick = str(detail.get("symbol") or "?").upper()
    rank = detail.get("market_cap_rank") or md.get("market_cap_rank")

    def pick(field: str) -> float | None:
        block = md.get(field) or {}
        if not isinstance(block, dict):
            return None
        val = block.get(vs)
        return val if val is not None else block.get("usd")

    price = pick("current_price")
    high = pick("high_24h")
    low = pick("low_24h")
    ath = pick("ath")
    ath_chg = (md.get("ath_change_percentage") or {}).get(vs)
    title = f"<b>{name} - ${tick}</b>"
    if rank:
        title += f" [{rank}]"
    lines = [
        title,
        f"💰 Price: {money(price, vs)}",
    ]
    if tick == "BTC" and price:
        sats = 100_000_000 / float(price)
        lines.append(f"ß $1.00 → {sats:.2f}")
    lines.extend(
        [
            f"⚖ H/L: {money(high, vs)} | {money(low, vs)}",
            _change_row("1h", (md.get("price_change_percentage_1h_in_currency") or {}).get(vs)),
            _change_row("24h", (md.get("price_change_percentage_24h_in_currency") or {}).get(vs)),
            _change_row("7d", (md.get("price_change_percentage_7d_in_currency") or {}).get(vs)),
            _change_row("30d", (md.get("price_change_percentage_30d_in_currency") or {}).get(vs)),
            f"🥇 ATH: {money(ath, vs)}" + (f" ({float(ath_chg):+.2f}%)" if ath_chg is not None else ""),
            f"💧 24h Vol: {money(pick('total_volume'), vs)}",
            f"💎 MCap: {money(pick('market_cap'), vs)}",
            "Gecko | Web | X",
        ]
    )
    return "\n".join(lines)


def price_keyboard(lang: str, coin_id: str, vs: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "btn_refresh"), callback_data=f"pr:{coin_id}:{vs}"),
                InlineKeyboardButton(t(lang, "btn_chart"), callback_data=f"pc:{coin_id}:{vs}"),
            ]
        ]
    )


async def fetch_coin_detail(coin_id: str) -> dict[str, Any]:
    return await cg_get(
        f"/coins/{coin_id}",
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        },
    )


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    args = _args(context)
    vs = vs_of(context)
    symbol = args[0] if args else ""
    if len(args) >= 2 and args[1].isalpha() and len(args[1]) == 3:
        vs = args[1].lower()
    coin = await _resolve_or_reply(update, context, symbol)
    if not coin:
        return
    try:
        detail = await fetch_coin_detail(coin["id"])
    except CoinGeckoError:
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    await update.effective_message.reply_html(
        format_phanes_price(detail, vs),
        reply_markup=price_keyboard(language, coin["id"], vs),
    )


async def cmd_ath(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    args = _args(context)
    vs = vs_of(context)
    coin = await _resolve_or_reply(update, context, args[0] if args else "")
    if not coin:
        return
    try:
        detail = await cg_get(
            f"/coins/{coin['id']}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
        )
    except CoinGeckoError:
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    md = detail.get("market_data") or {}
    await update.effective_message.reply_html(
        t(
            language,
            "ath_line",
            name=detail.get("name") or coin["name"],
            ath=money((md.get("ath") or {}).get(vs), vs),
            ath_date=day_label((md.get("ath_date") or {}).get(vs)),
            price=money((md.get("current_price") or {}).get(vs), vs),
            ath_change=pct((md.get("ath_change_percentage") or {}).get(vs)),
        )
    )


def chart_keyboard(symbol: str, interval: str, theme: str) -> InlineKeyboardMarkup:
    row = []
    for item in CHART_INTERVALS:
        label = f"• {item}" if item == interval else item
        row.append(InlineKeyboardButton(label, callback_data=f"ch:{symbol}:{item}:{theme}"))
    toggle = "Dark" if theme == "light" else "Light"
    next_theme = "dark" if theme == "light" else "light"
    return InlineKeyboardMarkup(
        [
            row,
            [InlineKeyboardButton(toggle, callback_data=f"ch:{symbol}:{interval}:{next_theme}")],
        ]
    )


async def build_chart(symbol: str, interval: str, theme: str) -> tuple[bytes, str]:
    candles = await fetch_binance_klines(symbol, interval)
    pair = f"{symbol.upper()}/USDT"
    title = f"BINANCE:{pair} · {interval}"
    png = render_ohlc_png(candles, title=title, vs="USDT", theme=theme)
    return png, title


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    args = _args(context)
    symbol = args[0] if args else ""
    interval = args[1].lower() if len(args) >= 2 else "1h"
    if interval not in CHART_INTERVALS:
        interval = "1h"
    theme = "light"
    coin = await _resolve_or_reply(update, context, symbol)
    if not coin:
        return
    tick = coin["symbol"]
    try:
        png, title = await build_chart(tick, interval, theme)
    except Exception:
        log.exception("chart failed")
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    await update.effective_message.reply_photo(
        photo=png,
        caption=title,
        reply_markup=chart_keyboard(tick, interval, theme),
    )


async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    try:
        data = await cg_get("/search/trending")
    except CoinGeckoError:
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    lines = [t(language, "trending_header")]
    for index, item in enumerate((data.get("coins") or [])[:10], start=1):
        item_coin = item.get("item") or item
        name = item_coin.get("name") or "?"
        symbol = str(item_coin.get("symbol") or "?").upper()
        rank = item_coin.get("market_cap_rank") or "—"
        lines.append(f"{index}. <b>{name}</b> ({symbol})  #{rank}")
    await update.effective_message.reply_html("\n".join(lines))


async def cmd_dom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    vs = vs_of(context)
    try:
        payload = await cg_get("/global")
    except CoinGeckoError:
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    data = payload.get("data") or {}
    pct_map = data.get("market_cap_percentage") or {}
    mcap_map = data.get("total_market_cap") or {}
    await update.effective_message.reply_html(
        t(
            language,
            "dom_line",
            btc=f"{float(pct_map.get('btc') or 0):.2f}",
            eth=f"{float(pct_map.get('eth') or 0):.2f}",
            mcap=money(mcap_map.get(vs) or mcap_map.get("usd"), vs),
        )
    )


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    settings = context.bot_data.get("settings") or {}
    key = str(settings.get("xai_api_key") or os.environ.get("XAI_API_KEY") or "").strip()
    question = " ".join(_args(context)).strip()
    message = update.effective_message
    if not question and message and message.reply_to_message:
        question = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    if not question:
        await message.reply_html(t(language, "ask_need"))
        return
    if not key:
        await message.reply_html(t(language, "ask_no_key"))
        return
    payload = {
        "model": "grok-3-mini",
        "messages": [
            {"role": "system", "content": "You are Grok, helpful and concise. Answer in the user's language."},
            {"role": "user", "content": question[:4000]},
        ],
        "temperature": 0.7,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        body = response.json()
        if response.status_code >= 400:
            raise RuntimeError(body)
        text = body["choices"][0]["message"]["content"]
    except Exception:
        log.exception("xAI /ask failed")
        await message.reply_html(t(language, "ask_error"))
        return
    await message.reply_text(text[:4000])


async def on_price_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    language = lang_of(context)
    kind, coin_id, vs = (query.data.split(":") + ["", ""])[:3]
    try:
        detail = await fetch_coin_detail(coin_id)
    except CoinGeckoError:
        await query.answer(t(language, "coingecko_error"), show_alert=True)
        return
    if kind == "pr":
        await query.answer("↻")
        if query.message:
            await query.message.edit_text(
                format_phanes_price(detail, vs),
                parse_mode="HTML",
                reply_markup=price_keyboard(language, coin_id, vs),
            )
        return
    await query.answer()
    try:
        candles = await cg_get(f"/coins/{coin_id}/ohlc", {"vs_currency": vs, "days": "7"})
        png = render_ohlc_png(candles, title=f"{detail.get('name')} 7d {vs.upper()}", vs=vs)
    except Exception:
        await query.message.reply_html(t(language, "coingecko_error"))
        return
    await query.message.reply_photo(photo=png)


async def on_chart_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    language = lang_of(context)
    _prefix, symbol, interval, theme = (query.data.split(":") + ["", "", ""])[:4]
    if interval not in CHART_INTERVALS:
        interval = "1h"
    if theme not in ("light", "dark"):
        theme = "light"
    try:
        png, title = await build_chart(symbol, interval, theme)
    except Exception:
        await query.answer(t(language, "coingecko_error"), show_alert=True)
        return
    await query.answer(interval)
    if query.message:
        await query.message.edit_media(
            media=InputMediaPhoto(media=png, caption=title),
            reply_markup=chart_keyboard(symbol, interval, theme),
        )


# ---------------------------------------------------------------------------
# Process entry
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    settings = load_settings()
    token = str(settings.get("bot_token") or "").strip()
    if not token:
        log.error("No bot_token — set it in Telegram Botmanager on Umbrel.")
        sys.exit(2)

    application = Application.builder().token(token).build()
    application.bot_data["settings"] = settings

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("p", cmd_price))
    application.add_handler(CommandHandler("c", cmd_chart))
    application.add_handler(CommandHandler("ath", cmd_ath))
    application.add_handler(CommandHandler("trending", cmd_trending))
    application.add_handler(CommandHandler("dom", cmd_dom))
    application.add_handler(CommandHandler("ask", cmd_ask))
    application.add_handler(CallbackQueryHandler(on_delete_button, pattern=r"^d:"))
    application.add_handler(CallbackQueryHandler(on_price_buttons, pattern=r"^p[rc]:"))
    application.add_handler(CallbackQueryHandler(on_chart_buttons, pattern=r"^ch:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_maybe_links))

    log.info("ThePirateBot polling Telegram")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
