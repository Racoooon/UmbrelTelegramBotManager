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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: E402
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
            "/c btc — chart (1h 4h 1d 7d 1w 1m)\n"
            "/ath btc — all-time high\n"
            "/trending — CoinGecko trending\n"
            "/dom — Bitcoin dominance\n"
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
            "/c btc — Chart (1h 4h 1d 7d 1w 1m)\n"
            "/ath btc — Allzeithoch\n"
            "/trending — Trend-Coins\n"
            "/dom — Bitcoin-Dominanz\n"
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


def fix_x(url: str) -> tuple[str, str]:
    """Return (fixupx_url, cleaned x.com url for the View Tweet button)."""
    parsed = urlparse(_strip_trailing_punct(url))
    view = urlunparse(("https", "x.com", parsed.path, "", "", ""))
    fixed = urlunparse(("https", "fixupx.com", parsed.path, "", "", ""))
    return fixed, view


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

    # One Telegram message per link so each View button points at the right URL.
    for item in items:
        await message.reply_text(
            item["fixed"],
            disable_web_page_preview=False,
            reply_markup=link_keyboard(language, poster_id, item["kind"], item["view"]),
        )

    if settings.get("delete_link_only_originals", True) and is_links_only(
        message.text, [i["original"] for i in items]
    ):
        try:
            await message.delete()
        except Exception:
            pass


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


def render_ohlc_png(candles: list[list[float]], title: str, vs: str) -> bytes:
    if not candles:
        raise ValueError("no candles")
    opens = [c[1] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    xs = [mdates.date2num(datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc)) for c in candles]

    fig, ax = plt.subplots(figsize=(10, 5), dpi=140)
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    delta = xs[1] - xs[0] if len(xs) > 1 else 0.03
    width = max(delta * 0.7, 0.0008)
    for i, x in enumerate(xs):
        color = "#22c55e" if closes[i] >= opens[i] else "#ef4444"
        ax.vlines(x, lows[i], highs[i], color=color, linewidth=1, zorder=1)
        body_low = min(opens[i], closes[i])
        body_height = max(abs(closes[i] - opens[i]), (highs[i] - lows[i]) * 0.002)
        ax.add_patch(Rectangle((x - width / 2, body_low), width, body_height, facecolor=color, edgecolor=color, zorder=2))
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    ax.set_title(title, color="#e5e7eb", fontsize=12, pad=10)
    ax.set_ylabel(vs.upper(), color="#9ca3af")
    ax.tick_params(colors="#9ca3af")
    for spine in ax.spines.values():
        spine.set_color("#374151")
    ax.grid(True, color="#1f2937", linestyle="--", linewidth=0.6)
    buf = io.BytesIO()
    fig.tight_layout()
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
    name = detail.get("name") or coin["name"]
    tick = (detail.get("symbol") or coin["symbol"]).upper()

    def pick(field: str) -> float | None:
        block = md.get(field) or {}
        return block.get(vs) or block.get("usd") if isinstance(block, dict) else None

    lines = [
        t(language, "price_header", name=name, symbol=tick),
        t(language, "price_now", price=money(pick("current_price"), vs)),
        t(
            language,
            "price_changes",
            h1=pct(md.get("price_change_percentage_1h_in_currency", {}).get(vs)),
            h24=pct(md.get("price_change_percentage_24h_in_currency", {}).get(vs)),
            d7=pct(md.get("price_change_percentage_7d_in_currency", {}).get(vs)),
            d30=pct(md.get("price_change_percentage_30d_in_currency", {}).get(vs)),
        ),
        t(
            language,
            "price_ath",
            ath=money(pick("ath"), vs),
            ath_date=day_label((md.get("ath_date") or {}).get(vs)),
            ath_change=pct((md.get("ath_change_percentage") or {}).get(vs)),
        ),
        t(language, "price_mcap", mcap=money(pick("market_cap"), vs)),
        t(language, "price_vol", vol=money(pick("total_volume"), vs)),
    ]
    rank = detail.get("market_cap_rank") or md.get("market_cap_rank")
    if rank:
        lines.append(t(language, "price_rank", rank=rank))
    await update.effective_message.reply_html("\n".join(lines))


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


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = lang_of(context)
    args = _args(context)
    vs = vs_of(context)
    symbol = args[0] if args else ""
    tf = args[1].lower() if len(args) >= 2 else "7d"
    days = TIMEFRAMES.get(tf, "7")
    coin = await _resolve_or_reply(update, context, symbol)
    if not coin:
        return
    try:
        candles = await cg_get(f"/coins/{coin['id']}/ohlc", {"vs_currency": vs, "days": days})
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
    if not isinstance(candles, list):
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    name = detail.get("name") or coin["name"]
    tick = (detail.get("symbol") or coin["symbol"]).upper()
    try:
        png = render_ohlc_png(candles, title=f"{name} ({tick})  {days}d  {vs.upper()}", vs=vs)
    except ValueError:
        await update.effective_message.reply_html(t(language, "coingecko_error"))
        return
    await update.effective_message.reply_photo(
        photo=png,
        caption=t(language, "chart_caption", name=name, symbol=tick, days=f"{days}d", vs=vs.upper()),
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
    application.add_handler(CallbackQueryHandler(on_delete_button, pattern=r"^d:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_maybe_links))

    log.info("ThePirateBot polling Telegram")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
