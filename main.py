import asyncio
import email
import html
import imaplib
import json
import logging
import os
import re
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.header import decode_header
from email.message import Message
from urllib.parse import parse_qs, urljoin, urlparse
try:
    from zoneinfo import ZoneInfo
    KYIV_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    KYIV_TZ = timezone(timedelta(hours=2))

import aiohttp
import feedparser
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiohttp import web
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from supabase import Client, create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("usa_drop_radar")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GAS_PROXY_URL = os.getenv("GAS_PROXY_URL")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_HOST = os.getenv("IMAP_HOST", "imap.ukr.net")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

RADAR_STANDARD_INTERVAL = int(os.getenv("RADAR_STANDARD_INTERVAL", "600"))
RADAR_REDDIT_INTERVAL = int(os.getenv("RADAR_REDDIT_INTERVAL", "900"))
TRACKER_INTERVAL = int(os.getenv("TRACKER_INTERVAL", "10800"))
EMAIL_CHECK_INTERVAL = int(os.getenv("EMAIL_CHECK_INTERVAL", "600"))
MIN_DISCOUNT_PCT = int(os.getenv("MIN_DISCOUNT_PCT", "35"))
MAX_PRICE_NO_DISCOUNT_USD = float(os.getenv("MAX_PRICE_NO_DISCOUNT_USD", "95"))
DEAL_DEDUP_DAYS = int(os.getenv("DEAL_DEDUP_DAYS", "7"))

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("❌ Ошибка: Не заданы переменные окружения (BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY)!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
http_session: aiohttp.ClientSession | None = None
EXCHANGE_RATES: dict[str, float] = {}
BOT_USERNAME: str | None = None

DEFAULT_PRICE_PLACEHOLDER = "🔥 СМОТРЕТЬ НА САЙТЕ"
RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; USADropRadarBot/2.1; +https://github.com/artdac-png/usa_drop_radar)",
    "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml;q=0.9, */*;q=0.8",
}

RECENT_DEAL_IDS: deque[str] = deque(maxlen=1000)
RECENT_DEAL_IDS_SET: set[str] = set()
RECENT_TITLES: deque[str] = deque(maxlen=1000)
RECENT_TITLES_SET: set[str] = set()

# Today's active store/brand promo codes (TTL = 24h), populated from RSS coupon feeds & store pages
PROMO_TTL_SECONDS = 86400
ACTIVE_STORE_PROMOS: dict[str, dict] = {}
NOTIFIED_PROMOS: set[tuple[int, str, str]] = set()
PENDING_EMAIL_PROMOS: dict[str, dict] = {}
EMAIL_PROMO_SEQ = 0
BOT_START_TS = time.time()
LAST_DIGEST_DATE: str | None = None

QUIET_START_HOUR = int(os.getenv("QUIET_START_HOUR", "23"))
QUIET_END_HOUR = int(os.getenv("QUIET_END_HOUR", "8"))
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "20"))

EMAIL_SERVICE_STOP_WORDS = (
    "order confirmation", "your order", "has shipped", "out for delivery",
    "has been delivered", "tracking number", "return label", "refund processed",
    "reset your password", "password reset", "verification code", "verify your email",
    "security alert", "sign-in attempt", "invoice #", "receipt for your",
    "your cart is waiting", "left something in your cart", "left in your cart",
    "still in your bag", "complete your purchase",
)


class BotState(StatesGroup):
    waiting_for_url = State()
    waiting_for_sizes = State()
    waiting_for_alert_keyword = State()
    waiting_for_search_query = State()


BRAND_ALIASES: list[tuple[str, str]] = [
    ("jordan", "Jordan"),
    ("nike", "Nike"),
    ("adidas", "Adidas"),
    ("puma", "Puma"),
    ("new balance", "New Balance"),
    ("under armour", "Under Armour"),
    ("converse", "Converse"),
    ("lacoste", "Lacoste"),
    ("tommy hilfiger", "Tommy Hilfiger"),
    ("tommy jeans", "Tommy Hilfiger"),
    ("calvin klein", "Calvin Klein"),
    ("calvin", "Calvin Klein"),
    ("guess", "Guess"),
    ("michael kors", "Michael Kors"),
    ("kors", "Michael Kors"),
    ("coach", "Coach"),
    ("levi's", "Levi's"),
    ("levis", "Levi's"),
    ("levi", "Levi's"),
    ("hugo boss", "Hugo Boss"),
    ("zara", "Zara"),
    ("mango", "Mango"),
    ("massimo dutti", "Massimo Dutti"),
    ("massimo", "Massimo Dutti"),
    ("h&m", "H&M"),
    ("h m", "H&M"),
    ("hm", "H&M"),
    ("asics", "Asics"),
    ("salomon", "Salomon"),
    ("solomon", "Salomon"),
    ("saucony", "Saucony"),
    ("soucone", "Saucony"),
    ("mizuno", "Mizuno"),
    ("hoka", "Hoka"),
    ("on running", "On Running"),
    ("onrunning", "On Running"),
    ("on-running", "On Running"),
    ("the north face", "The North Face"),
    ("north face", "The North Face"),
    ("tnf", "The North Face"),
    ("joes new balance", "New Balance"),
    ("joe's new balance", "New Balance"),
    ("gap", "Gap"),
    ("old navy", "Old Navy"),
]

COMPILED_BRANDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE), canonical)
    for alias, canonical in BRAND_ALIASES
]

EXCLUDE_STOP_WORDS = [
    "watch", "watches", "chronograph", "timepiece", "smartwatch", "quartz", "dial", "clock",
    "headphone", "headphones", "earbuds", "earphone", "headset", "speaker",
    "laptop", "tv", "television", "monitor", "phone", "smartphone", "audio",
    "amplifier", "soundbar", "drive", "ssd", "gpu", "cpu",
    "perfume", "fragrance", "cologne", "eau de", "parfum", "toilette",
    "giftset", "gift set", "body wash", "shower gel", "deodorant", "shampoo", "lotion", "cream", "soap", "skincare", "beauty",
    "kids", "kid's", "toddler", "infant", "youth", "baby", "child", "children",
    "boy", "boys", "boy's", "girl", "girls", "girl's",
    "gs", "ps", "td", "preschool", "grade school",
    "expired", "dead deal", "sold out", "deal ended", "ended", "no longer available",
    "lokal", "in-store only", "in store only", "ymmv",
    "keyboard", "wagon", "gaming", "computer", "mouse", "toy", "toys", "tools", "kitchen", "vacuum", "printer",
    "controller", "nintendo", "switch", "playstation", "ps5", "ps4", "xbox", "mario", "luigi", "lego", "console",
    "coffee", "coffeeware", "nespresso", "starbucks", "espresso", "tea", "snack", "snacks", "grocery", "food", "candy", "chocolate",
    "mattress", "pillow", "furniture", "bedding", "blanket", "towel", "luggage", "suitcase",
    "water bottle", "bottle", "hydro flask", "tumbler", "mug", "thermos", "flask",
    "block heel", "heel", "heels", "stiletto", "western boot", "cowboy",
    "pet", "dog", "cat", "vitamin", "supplement", "protein", "diaper", "stroller", "tire",
    "battery", "charger", "cable", "router", "camera", "lens", "drone", "guitar", "pedal",
    "dehumidifier", "frigidaire", "air filter", "appliance", "heater", "cooler", "fridge", "refrigerator", "washing", "dryer", "microwave", "oven", "blender",
]

STOP_WORDS_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in EXCLUDE_STOP_WORDS) + r")\b",
    re.IGNORECASE,
)

STANDARD_RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+jordan+adidas+lacoste+tommy+calvin+asics+salomon+saucony+mizuno+hoka+onrunning+levis+northface+shoebacca+macys+gap+oldnavy&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=shoebacca&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=adidas&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=new+balance&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=joes+new+balance&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=north+face&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=levis&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=asics&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=hoka&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=salomon&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=calvin+klein&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=tommy+hilfiger&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=gap&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=6pm&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=sierra&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nordstrom+rack&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=ebay+adidas&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=ebay+puma&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.dealnews.com/c196/Clothing-Accessories/?rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.dealnews.com/c202/Clothing-Accessories/Shoes/?rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://sneakersteal.com/feed/"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.hotukdeals.com/rss/tag/fashion"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.hotukdeals.com/rss/tag/sports-fitness"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.hotukdeals.com/rss/tag/trainers"},
    {"flag": "🇩🇪 [Германия / DE]", "url": "https://www.mydealz.de/rss/gruppe/fashion"},
    {"flag": "🇩🇪 [Германия / DE]", "url": "https://www.mydealz.de/rss/gruppe/sport"},
    {"flag": "🇩🇪 [Германия / DE]", "url": "https://www.mydealz.de/rss/gruppe/sneaker"},
    {"flag": "🇪🇸 [Испания / ES]", "url": "https://www.chollometro.com/rss/grupo/moda-y-accesorios"},
    {"flag": "🇪🇸 [Испания / ES]", "url": "https://www.chollometro.com/rss/grupo/deportes"},
    {"flag": "🇫🇷 [Франция / FR]", "url": "https://www.dealabs.com/rss/groupe/mode-et-accessoires"},
    {"flag": "🇮🇹 [Италия / IT]", "url": "https://www.pepper.it/rss/gruppo/moda-e-accessori"},
    {"flag": "🇵🇱 [Польша / PL]", "url": "https://www.pepper.pl/rss/grupa/moda-i-akcesoria"},
]

REDDIT_RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/frugalmalefashion/.rss"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/frugalfemalefashion/.rss"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/SneakerDeals/.rss"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/RunningShoeGeeks/.rss"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.reddit.com/r/FrugalMaleFashionUK/.rss"},
]

PROMO_STOP_WORDS = {
    "CODE", "OFF", "FREE", "SHIP", "SHIPPING", "SITE", "SITEWIDE", "ONLY",
    "WITH", "HERE", "MORE", "CART", "DEAL", "SALE", "LINK", "PAGE", "THIS",
    "THAT", "FROM", "YOUR", "SAVE", "EXTRA", "APPLY", "CHECKOUT", "ONLINE",
}

MENU_BUTTONS = {
    "➕ Добавить ссылку",
    "📋 Мой список",
    "🔄 Проверить всё",
    "🔔 Алерты",
    "🔍 Поиск",
    "💱 Курсы валют",
    "ℹ️ Помощь",
    "❌ Отмена",
}


async def db_exec(fn):
    """Run synchronous Supabase SDK calls in a worker thread to avoid blocking the event loop."""
    return await asyncio.to_thread(fn)


async def safe_update_tracked_item(item_id, fields: dict) -> None:
    """Update tracked_items gracefully; falls back to core columns if new migration columns aren't created yet."""
    try:
        await db_exec(lambda: supabase.table("tracked_items").update(fields).eq("id", item_id).execute())
    except Exception as e:
        core_fields = {k: v for k, v in fields.items() if k in ("last_price", "image_url", "title", "brand")}
        if core_fields and core_fields != fields:
            logger.warning("⚠️ Новые колонки tracked_items ещё не созданы в Supabase (%s). Обновляю базовые поля.", e)
            try:
                await db_exec(lambda: supabase.table("tracked_items").update(core_fields).eq("id", item_id).execute())
            except Exception as e2:
                logger.error("DB fallback update error: %s", e2)
        else:
            logger.error("DB update error: %s", e)


def remember_deal(deal_id: str, title: str) -> None:
    if deal_id and deal_id not in RECENT_DEAL_IDS_SET:
        if len(RECENT_DEAL_IDS) == RECENT_DEAL_IDS.maxlen:
            oldest = RECENT_DEAL_IDS.popleft()
            RECENT_DEAL_IDS_SET.discard(oldest)
        RECENT_DEAL_IDS.append(deal_id)
        RECENT_DEAL_IDS_SET.add(deal_id)
    if title and title not in RECENT_TITLES_SET:
        if len(RECENT_TITLES) == RECENT_TITLES.maxlen:
            oldest = RECENT_TITLES.popleft()
            RECENT_TITLES_SET.discard(oldest)
        RECENT_TITLES.append(title)
        RECENT_TITLES_SET.add(title)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"&reg;|®|&trade;|™", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def esc(text: str | None) -> str:
    return html.escape(clean_text(text))


def brand_to_hashtag(brand: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", brand.upper())
    return f"#{cleaned}" if cleaned else "#DROP"


def find_matched_brand(text: str | None) -> str | None:
    if not text:
        return None
    if STOP_WORDS_REGEX.search(text):
        return None
    for pattern, canonical in COMPILED_BRANDS:
        if pattern.search(text):
            return canonical
    text_lower = text.lower()
    if "calvinklein" in text_lower:
        return "Calvin Klein"
    if "levi.com" in text_lower:
        return "Levi's"
    return None


async def load_exchange_rates() -> None:
    global EXCHANGE_RATES
    if not http_session:
        return
    try:
        async with http_session.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("result") == "success":
                    rates = data["rates"]
                    EXCHANGE_RATES = {
                        "USD": 1.0,
                        "GBP": float(rates.get("GBP", 0.79)),
                        "EUR": float(rates.get("EUR", 0.92)),
                        "UAH": float(rates.get("UAH", 41.5)),
                    }
                    logger.info(
                        "💱 Курсы валют обновлены: 1 USD = %.2f UAH | %.2f EUR | %.2f GBP",
                        EXCHANGE_RATES["UAH"],
                        EXCHANGE_RATES["EUR"],
                        EXCHANGE_RATES["GBP"],
                    )
    except Exception as e:
        logger.warning("⚠️ Ошибка загрузки курсов валют: %s", e)


def parse_price_amount(price_str: str | None) -> tuple[float | None, str | None]:
    if not price_str:
        return None, None
    match = re.search(r"([$£€])\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*([$£€])", price_str)
    if not match:
        return None, None
    symbol = match.group(1) if match.group(1) else match.group(4)
    amount_str = match.group(2) if match.group(1) else match.group(3)
    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        return None, None
    currency_map = {"$": "USD", "£": "GBP", "€": "EUR"}
    return amount, currency_map.get(symbol)


def compute_min_price(old_min: str | None, new_price: str | None) -> str | None:
    if not new_price or new_price == DEFAULT_PRICE_PLACEHOLDER:
        return old_min
    if not old_min or old_min == DEFAULT_PRICE_PLACEHOLDER:
        return new_price
    old_val, old_cur = parse_price_amount(old_min)
    new_val, new_cur = parse_price_amount(new_price)
    if old_val is not None and new_val is not None and old_cur == new_cur:
        return new_price if new_val < old_val else old_min
    return old_min


def format_price_multi(price_str: str) -> str:
    if not price_str or not EXCHANGE_RATES:
        return price_str
    amount, base = parse_price_amount(price_str)
    if amount is None or not base or base not in EXCHANGE_RATES:
        return price_str
    usd_amount = amount if base == "USD" else amount / EXCHANGE_RATES[base]
    gbp_amount = usd_amount * EXCHANGE_RATES["GBP"]
    eur_amount = usd_amount * EXCHANGE_RATES["EUR"]
    uah_amount = usd_amount * EXCHANGE_RATES["UAH"]
    parts = []
    if base != "USD":
        parts.append(f"${usd_amount:.2f}")
    if base != "GBP":
        parts.append(f"£{gbp_amount:.2f}")
    if base != "EUR":
        parts.append(f"€{eur_amount:.2f}")
    return f"{price_str} (~{' / '.join(parts)} / ~{int(round(uah_amount))} грн)"


def extract_promo_code(title: str, summary: str) -> str | None:
    """Extract promo/coupon code like 'code EXTRA20', 'w/ code SAVE30', 'promo: FALL25'."""
    text = f"{title} {clean_text(re.sub(r'<[^>]+>', ' ', summary or ''))}"
    patterns = [
        r"(?:promo\s*code|coupon\s*code|use\s*code|w/\s*code|with\s*code|code|промокод)\s*[:#\"']?\s*([A-Z0-9][A-Z0-9_-]{2,19})\b",
    ]
    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            code = match.group(1).strip().upper()
            if code in PROMO_STOP_WORDS:
                continue
            # Prefer codes that have at least one digit or are explicitly uppercase in the source text
            raw_token = match.group(1)
            if any(ch.isdigit() for ch in code) or raw_token.isupper():
                return code
    return None


def extract_discount_pct(title: str, summary: str) -> int | None:
    clean_summary = clean_text(re.sub(r"<[^>]+>", " ", summary or ""))
    clean_summary = re.sub(r"https?://\S+", " ", clean_summary)
    text = f"{title} {clean_summary}"
    patterns = [
        r"\b(\d{1,2})\s*%\s*(?:off|rabatt|descuento|sconto|r[eé]duction|zni[zż]k|скидк|від|от)\b",
        r"\b(?:extra|save|sparen|rabatt|скидка)\s+(\d{1,2})\s*%",
        r"(?<![A-Za-z0-9])-(\d{1,2})\s*%",
    ]
    pcts = []
    for pat in patterns:
        for m in re.findall(pat, text, re.IGNORECASE):
            val = int(m)
            if 5 <= val <= 85:
                pcts.append(val)
    return max(pcts) if pcts else None


def extract_discount_fixed(title: str, summary: str) -> tuple[float | None, str | None]:
    """Extract fixed discount like '$20 off' or '€15 off'."""
    text = f"{title} {summary}"
    m = re.search(r"([$£€])\s*(\d+(?:\.\d{1,2})?)\s*off\b", text, re.IGNORECASE)
    if m:
        cur_map = {"$": "USD", "£": "GBP", "€": "EUR"}
        return float(m.group(2)), cur_map.get(m.group(1))
    return None, None


def extract_discount_from_compare_price(title: str, summary: str, current_price_str: str) -> int | None:
    """Calculate discount % from reference prices like 'PVG 140€', 'UVP 150€', 'Reg. $120', 'Was $110'."""
    cur_amt, cur_code = parse_price_amount(current_price_str)
    if cur_amt is None or not cur_code or cur_amt <= 0:
        return None
    clean_sum = clean_text(re.sub(r"<[^>]+>", " ", summary or ""))
    text = f"{title} {clean_sum}"
    matches = re.findall(
        r"\b(?:pvg|uvp|reg\.?|list|was|orig\.?|msrp|rrp)\s*[:\-]?\s*([$£€]\s*\d+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?\s*[$£€])",
        text,
        re.IGNORECASE,
    )
    best_pct = None
    for ref_str in matches:
        ref_amt, ref_code = parse_price_amount(ref_str)
        if ref_amt and ref_code == cur_code and ref_amt > cur_amt:
            pct = int(round((ref_amt - cur_amt) / ref_amt * 100))
            if 5 <= pct <= 90 and (best_pct is None or pct > best_pct):
                best_pct = pct
    return best_pct


def price_to_usd(price_str: str | None) -> float | None:
    amt, code = parse_price_amount(price_str)
    if amt is None or not code:
        return None
    if code == "USD":
        return amt
    rate = EXCHANGE_RATES.get(code)
    if rate and rate > 0:
        return amt / rate
    fallback = {"EUR": 0.92, "GBP": 0.79}.get(code, 1.0)
    return amt / fallback


def extract_page_promo(page_html: str) -> dict | None:
    """Extract an active store promo code + discount from visible text on a product page."""
    if not page_html:
        return None
    visible = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", page_html, flags=re.S | re.I)
    visible = clean_text(re.sub(r"<[^>]+>", " ", visible))
    patterns = [
        r"(?:extra\s+|take\s+|get\s+|save\s+)?(\d{1,2})\s*%\s*off.{0,70}?(?:use\s+|with\s+|w/\s*)?(?:promo\s+|coupon\s+)?code[:\s\"']+([A-Z0-9][A-Z0-9_-]{2,18})\b",
        r"(?:use\s+|with\s+|w/\s*)?(?:promo\s+|coupon\s+)?code[:\s\"']+([A-Z0-9][A-Z0-9_-]{2,18})\b.{0,70}?(?:for\s+|to\s+get\s+|to\s+save\s+)?(?:extra\s+)?(\d{1,2})\s*%\s*off",
    ]
    for idx, pat in enumerate(patterns):
        for match in re.finditer(pat, visible, re.IGNORECASE):
            if idx == 0:
                pct = int(match.group(1))
                raw_code = match.group(2)
            else:
                raw_code = match.group(1)
                pct = int(match.group(2))
            code = raw_code.strip().upper()
            if code in PROMO_STOP_WORDS or not (5 <= pct <= 80):
                continue
            if any(ch.isdigit() for ch in code) or raw_code.isupper():
                return {"code": code, "pct": pct, "fixed": None, "fixed_cur": None, "source": "сайт магазина", "ts": time.time()}
    return None


def is_quiet_hours() -> bool:
    """Return True during Kyiv night quiet hours (default 23:00–08:00)."""
    hour = datetime.now(KYIV_TZ).hour
    if QUIET_START_HOUR > QUIET_END_HOUR:
        return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR
    return QUIET_START_HOUR <= hour < QUIET_END_HOUR


async def load_active_promos_from_db() -> None:
    """Load non-expired store promos from Supabase on startup so promos survive restarts without re-notifying."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await db_exec(
            lambda: supabase.table("active_promos").select("*").gt("expires_at", now_iso).execute()
        )
        loaded = 0
        for row in res.data or []:
            key = (row.get("brand") or "").strip().lower()
            code = (row.get("code") or "").strip().upper()
            if not key or not code:
                continue
            exp_ts = time.time() + PROMO_TTL_SECONDS
            try:
                exp_dt = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                exp_ts = exp_dt.timestamp()
            except Exception:
                pass
            ACTIVE_STORE_PROMOS[key] = {
                "code": code,
                "pct": row.get("pct"),
                "fixed": row.get("fixed"),
                "fixed_cur": row.get("fixed_cur"),
                "source": row.get("source") or "DB",
                "ts": exp_ts - PROMO_TTL_SECONDS,
            }
            loaded += 1
        if loaded:
            logger.info("🎟 Загружено %d активных промокодов из Supabase.", loaded)
    except Exception as e:
        logger.debug("active_promos load skipped (table may not exist yet): %s", e)


def _schedule_promo_persist(brand_key: str, promo: dict) -> None:
    async def _save():
        try:
            expires_iso = datetime.fromtimestamp(promo["ts"] + PROMO_TTL_SECONDS, tz=timezone.utc).isoformat()
            await db_exec(
                lambda: supabase.table("active_promos")
                .upsert({
                    "brand": brand_key,
                    "code": promo["code"],
                    "pct": promo.get("pct"),
                    "fixed": promo.get("fixed"),
                    "fixed_cur": promo.get("fixed_cur"),
                    "source": promo.get("source") or "RSS",
                    "expires_at": expires_iso,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, on_conflict="brand")
                .execute()
            )
        except Exception as e:
            logger.debug("active_promos save skipped: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_save())
    except RuntimeError:
        pass


async def delete_promo_from_db(brand_key: str) -> None:
    try:
        await db_exec(lambda: supabase.table("active_promos").delete().eq("brand", brand_key).execute())
    except Exception:
        pass


def register_store_promo(
    brand: str,
    code: str,
    pct: int | None = None,
    fixed: float | None = None,
    fixed_cur: str | None = None,
    source: str = "RSS",
) -> bool:
    """Store today's promo for a brand. Returns True if it's a newly discovered promo code."""
    if not brand or not code:
        return False
    key = brand.strip().lower()
    now = time.time()
    existing = ACTIVE_STORE_PROMOS.get(key)
    if existing and (now - existing["ts"]) < PROMO_TTL_SECONDS:
        if existing["code"] == code:
            if pct and (not existing.get("pct") or pct > existing["pct"]):
                existing["pct"] = pct
                _schedule_promo_persist(key, existing)
            existing["ts"] = now
            return False
        # Keep the better percentage discount if another code is already active today
        if existing.get("pct") and pct and pct < existing["pct"]:
            return False

    promo = {
        "code": code,
        "pct": pct,
        "fixed": fixed,
        "fixed_cur": fixed_cur,
        "source": source,
        "ts": now,
    }
    ACTIVE_STORE_PROMOS[key] = promo
    _schedule_promo_persist(key, promo)
    return True


def get_active_promo_for_item(item: dict, page_promo: dict | None = None) -> dict | None:
    if page_promo and page_promo.get("code"):
        return page_promo
    now = time.time()
    candidates = []
    if item.get("brand"):
        candidates.append(item["brand"].strip().lower())
    url_brand = find_matched_brand(item.get("url"))
    if url_brand:
        candidates.append(url_brand.strip().lower())
    for key in candidates:
        promo = ACTIVE_STORE_PROMOS.get(key)
        if promo and (now - promo["ts"]) < PROMO_TTL_SECONDS:
            return promo
    return None


def apply_promo_to_price(price_str: str | None, promo: dict | None) -> str | None:
    if not price_str or not promo:
        return None
    amount, base = parse_price_amount(price_str)
    if amount is None or not base:
        return None
    symbol_map = {"USD": "$", "GBP": "£", "EUR": "€"}
    symbol = symbol_map.get(base, "$")
    discounted = None
    if promo.get("pct") and 1 <= promo["pct"] <= 95:
        discounted = amount * (1.0 - promo["pct"] / 100.0)
    elif promo.get("fixed") and promo.get("fixed_cur") == base and amount > promo["fixed"]:
        discounted = amount - promo["fixed"]
    if discounted is None or discounted <= 0:
        return None
    base_discounted = f"{symbol}{discounted:.2f}"
    return format_price_multi(base_discounted)


def format_promo_lines(price_str: str | None, promo: dict | None) -> list[str]:
    if not promo or not promo.get("code"):
        return []
    code = promo["code"]
    disc_desc = ""
    if promo.get("pct"):
        disc_desc = f" (-{promo['pct']}%)"
    elif promo.get("fixed") and promo.get("fixed_cur"):
        sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(promo["fixed_cur"], "$")
        disc_desc = f" (-{sym}{promo['fixed']:.0f})"
    lines = [f"🎟 <b>Промокод сегодня:</b> <code>{esc(code)}</code>{disc_desc}"]
    discounted_multi = apply_promo_to_price(price_str, promo)
    if discounted_multi:
        lines.append(f"🔥 <b>С промокодом сейчас:</b> <code>{esc(discounted_multi)}</code>")
    return lines


def extract_price(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    spend_match = re.findall(r"(\d+%\s*(?:off|від|от)\s*(?:[\$\d\.\,€£\s]+|\$\d+\+?))", text, re.IGNORECASE)
    if spend_match:
        return f"🔥 СЕЙЛ ({spend_match[0].strip().upper()})"
    bundle_match = re.findall(r"(\b(?:buy\s*)?\d+\s*(?:for|por)\s*[\$\d\.\,€£\s]+(?:\$\d+|\d+€|\d+£)?)", text, re.IGNORECASE)
    if bundle_match:
        return bundle_match[0].strip()
    price_pattern = r"(\$\d+(?:\.\d{1,2})?|\b\d+(?:[.,]\d{1,2})?\s*€|€\s*\d+(?:[.,]\d{1,2})?|£\d+(?:\.\d{1,2})?)"
    match_title = re.findall(price_pattern, title)
    if match_title:
        return match_title[0]
    if summary:
        match_summary = re.findall(price_pattern, summary)
        if match_summary:
            return match_summary[0]
    pct_match = re.findall(r"(\d+%\s*off|extra\s*\d+%)", text, re.IGNORECASE)
    if pct_match:
        return f"🔥 СЕЙЛ ({pct_match[0].strip().upper()})"
    return "🔥 СЕЙЛ НА САЙТЕ"


def extract_best_link(summary: str, default_link: str) -> str:
    if not summary:
        return default_link
    urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', summary, re.IGNORECASE)
    redirect_kws = ["sdtid", "buyfromdeal", "pno=", "lw/", "redirect", "out/", "checkout", "couponcabin", "bradsdeals"]
    for url in urls:
        if any(kw in url.lower() for kw in redirect_kws):
            return html.unescape(url)
    forum_domains = [
        "slickdeals.net", "mydealz.de", "chollometro.com", "hotukdeals.com",
        "dealabs.com", "pepper.pl", "pepper.it", "dealnews.com",
        "bradsdeals.com", "couponcabin.com", "reddit.com",
    ]
    for url in urls:
        if not any(domain in url.lower() for domain in forum_domains):
            return html.unescape(url)
    return default_link


def extract_hd_image(entry, summary: str) -> str | None:
    url = None
    if hasattr(entry, "media_content") and entry.media_content:
        url = entry.media_content[0].get("url")
    elif hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
    elif hasattr(entry, "enclosures") and entry.enclosures:
        url = entry.enclosures[0].get("href")

    raw_html = ""
    if hasattr(entry, "content") and entry.content:
        raw_html += entry.content[0].get("value", "")
    if summary:
        raw_html += " " + summary

    if not url:
        reddit_img_matches = re.findall(r'(https?://(?:i|preview|external-preview)\.redd\.it/[^\s"\'<>]+)', raw_html, re.IGNORECASE)
        if reddit_img_matches:
            url = reddit_img_matches[0].replace("&amp;", "&")

    if not url and raw_html:
        img_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.IGNORECASE)
        for img_url in img_matches:
            if not any(stop in img_url.lower() for stop in ["beacon", "tracking", ".gif", "1x1", "avatar", "logo"]):
                url = img_url
                break

    if not url:
        return None
    url = html.unescape(url).strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://slickdeals.net" + url

    if "slickdeals.net" in url:
        url = re.sub(r"/\d+x\d+/", "/800x800/", url)
        url = re.sub(r"_(?:thumb|small|medium)\.", ".", url)
    elif any(domain in url for domain in ["pepper.com", "static-pepper", "hotukdeals", "mydealz", "chollometro", "dealabs"]):
        url = re.sub(r"/re/\d+x\d+/qt/\d+/", "/", url)
    return url


def extract_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    ignore_words = {
        "en_us", "us", "en", "clothing", "men", "women", "apparel",
        "mens-outerwear", "outerwear", "pants", "shoes", "p", "uk", "dp", "product", "products",
    }
    for part in reversed(path_parts):
        cleaned_part = re.sub(r"\.html?$", "", part, flags=re.IGNORECASE)
        if re.search(r"\d", cleaned_part) and len(cleaned_part) < 20:
            continue
        if len(cleaned_part) > 3 and cleaned_part.lower() not in ignore_words and not cleaned_part.isdigit():
            return cleaned_part.replace("-", " ").replace("_", " ").title()
    return "Брендовая вещь"


def extract_levis_image(url: str) -> str | None:
    sku_match = re.search(r"/p/([A-Za-z0-9]+)", url)
    if sku_match:
        return f"https://lsco.scene7.com/is/image/lsco/{sku_match.group(1)}-front-pdp?wid=800&hei=800&fmt=jpeg&qlt=85"
    return None


def extract_tnf_image(url: str) -> str | None:
    sku_match = re.search(r"(NF0A[A-Za-z0-9]+)", url, re.IGNORECASE)
    if not sku_match:
        return None
    sku = sku_match.group(1).upper()
    qs = parse_qs(urlparse(url).query)
    color = (qs.get("color") or qs.get("colour") or [""])[0].strip().upper()
    if color:
        return f"https://assets.thenorthface.com/image/upload/f_jpg,w_800/{sku}{color}-HERO"
    return f"https://assets.thenorthface.com/image/upload/f_jpg,w_800/{sku}-HERO"


def normalize_image_url(img_url: str | None, page_url: str) -> str | None:
    if not img_url:
        return None
    img_url = html.unescape(img_url).strip()
    if not img_url or img_url.startswith("data:"):
        return None
    if img_url.startswith("//"):
        return "https:" + img_url
    if img_url.startswith("/"):
        return urljoin(page_url, img_url)
    return img_url


def extract_meta_content(page_html: str, prop_name: str) -> str | None:
    escaped = re.escape(prop_name)
    patterns = [
        rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
    ]
    for pat in patterns:
        match = re.search(pat, page_html, re.IGNORECASE)
        if match:
            return html.unescape(match.group(1)).strip()
    return None


def extract_page_image(page_html: str, page_url: str) -> str | None:
    for prop in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        val = extract_meta_content(page_html, prop)
        norm = normalize_image_url(val, page_url)
        if norm:
            return norm

    json_img = (
        re.search(r'"image"\s*:\s*"(https?:\\?/\\?/[^"\\]+(?:\.(?:jpg|jpeg|png|webp)[^"\\]*|[^"\\]*))"', page_html, re.IGNORECASE)
        or re.search(r'"image"\s*:\s*\[\s*"(https?:\\?/\\?/[^"\\]+)"', page_html, re.IGNORECASE)
    )
    if json_img:
        candidate = json_img.group(1).replace("\\/", "/")
        norm = normalize_image_url(candidate, page_url)
        if norm and not any(bad in norm.lower() for bad in ("logo", "icon", "sprite", "placeholder", ".svg")):
            return norm

    link_img = re.search(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', page_html, re.IGNORECASE)
    if link_img:
        return normalize_image_url(link_img.group(1), page_url)

    return None


def is_out_of_stock(page_html: str) -> bool:
    if re.search(r'"availability"\s*:\s*"(?:https?://schema\.org/)?OutOfStock"', page_html, re.IGNORECASE):
        if re.search(r'"availability"\s*:\s*"(?:https?://schema\.org/)?InStock"', page_html, re.IGNORECASE):
            return False
        return True
    if re.search(r'<meta[^>]+availability[^>]+content=["\'](?:out of stock|oos|sold out)["\']', page_html, re.IGNORECASE):
        return True
    return False


def detect_currency_symbol(page_html: str, url: str) -> str:
    currency_match = (
        re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', page_html, re.IGNORECASE)
        or re.search(r'(?:og|product):price:currency["\'][^>]+content=["\']([A-Z]{3})["\']', page_html, re.IGNORECASE)
    )
    if currency_match:
        code = currency_match.group(1).upper()
        if code == "EUR":
            return "€"
        if code == "GBP":
            return "£"
        if code == "USD":
            return "$"
    host = urlparse(url).netloc.lower()
    if host.endswith(".co.uk") or ".uk" in host:
        return "£"
    if any(host.endswith(tld) for tld in (".de", ".fr", ".es", ".it", ".eu", ".nl")):
        return "€"
    return "$"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = item.strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


async def fetch_nike_sizes(page_html: str, url: str, session: AsyncSession) -> tuple[list[str], bool | None]:
    """Parse Nike __NEXT_DATA__ + available_gtins/v3 API to get exact in-stock sizes for any Nike shoes or apparel."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    if not m:
        return [], None
    try:
        data = json.loads(m.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        selected = page_props.get("selectedProduct") or {}
        sizes_meta = selected.get("sizes") or []
        style_color = selected.get("styleColor")
        if not style_color:
            # Extract styleColor from URL tail like .../CD6404-002
            path_tail = urlparse(url).path.rstrip("/").split("/")[-1]
            if re.match(r"^[A-Z0-9]{5,7}-[A-Z0-9]{3}$", path_tail, re.IGNORECASE):
                style_color = path_tail.upper()
        if not sizes_meta or not style_color:
            return [], None

        gtin_to_size: dict[str, tuple[int, str]] = {}
        for idx, s in enumerate(sizes_meta):
            label = (s.get("localizedLabel") or s.get("label") or "").strip()
            seq = s.get("sortSequence", idx)
            for g in s.get("gtins") or []:
                gtin = g.get("gtin")
                if gtin and label:
                    gtin_to_size[gtin] = (seq, label)

        path_lower = urlparse(url).path.lower()
        merch_group = "EU" if any(seg in path_lower for seg in ("/gb/", "/de/", "/fr/", "/es/", "/it/", "/pl/", "/nl/")) else "US"
        api_url = f"https://api.nike.com/deliver/available_gtins/v3?filter=styleColor({style_color})&filter=merchGroup({merch_group})"
        api_resp = await session.get(api_url, impersonate="chrome", timeout=10)
        if api_resp.status_code != 200:
            return [], None

        api_data = api_resp.json()
        available_pairs: list[tuple[int, str]] = []
        for obj in api_data.get("objects") or []:
            if obj.get("available") is True:
                gtin = obj.get("gtin")
                if gtin in gtin_to_size:
                    available_pairs.append(gtin_to_size[gtin])
        available_pairs.sort(key=lambda x: x[0])
        available_sizes = _dedupe_preserve_order([lbl for _, lbl in available_pairs])
        return available_sizes, len(available_sizes) > 0
    except Exception as e:
        logger.debug("Nike size parse error for %s: %s", url, e)
        return [], None


async def fetch_nike_active_promo() -> dict | None:
    """Fetch Nike's official active promo code from nike.com promo banner / sale pages."""
    candidate_urls = ["https://www.nike.com/w/sale-3yaep"]
    try:
        async with AsyncSession() as s:
            # First discover active promo banner links from nike.com (e.g. Black Friday / Cyber Monday / Extra % Off)
            home = await s.get("https://www.nike.com/", impersonate="chrome", timeout=15)
            if home.status_code == 200:
                p = extract_page_promo(home.text)
                if p:
                    register_store_promo("Nike", p["code"], pct=p.get("pct"), source="nike.com")
                    return p
                for m in re.finditer(r'href="(https://www\.nike\.com/w/[^"]+)"[^>]*>[^<]{0,60}%\s*Off', home.text, re.I):
                    if m.group(1) not in candidate_urls:
                        candidate_urls.insert(0, m.group(1))
                for m in re.finditer(r'href="(/w/[^"]*(?:black-friday|cyber|sale|promo|extra)[^"]*)"', home.text, re.I):
                    full_u = "https://www.nike.com" + m.group(1)
                    if full_u not in candidate_urls:
                        candidate_urls.append(full_u)

            for promo_url in candidate_urls[:4]:
                resp = await s.get(promo_url, impersonate="chrome", timeout=15)
                if resp.status_code == 200:
                    p = extract_page_promo(resp.text)
                    if p:
                        register_store_promo("Nike", p["code"], pct=p.get("pct"), source="nike.com")
                        logger.info("🎟 Найден официальный промокод Nike: %s (-%s%%)", p["code"], p.get("pct"))
                        return p
    except Exception as e:
        logger.debug("Nike active promo check error: %s", e)
    return None


async def fetch_adidas_sizes(url: str, session: AsyncSession) -> tuple[list[str], bool | None]:
    """Fetch in-stock sizes from Adidas availability API if accessible."""
    sku_match = re.search(r"/([A-Z0-9]{6})\.html", url, re.IGNORECASE)
    if not sku_match:
        return [], None
    sku = sku_match.group(1).upper()
    parsed = urlparse(url)
    api_url = f"{parsed.scheme}://{parsed.netloc}/api/products/{sku}/availability"
    try:
        resp = await session.get(api_url, impersonate="chrome", timeout=10)
        if resp.status_code != 200:
            return [], None
        data = resp.json()
        sizes = [
            str(v.get("size")).strip()
            for v in (data.get("variation_list") or [])
            if v.get("availability_status") == "IN_STOCK" and v.get("size")
        ]
        sizes = _dedupe_preserve_order(sizes)
        return sizes, len(sizes) > 0
    except Exception as e:
        logger.debug("Adidas size API error for %s: %s", url, e)
        return [], None


def extract_generic_sizes(page_html: str) -> list[str]:
    """Extract in-stock sizes from Gap Inc. buybox HTML, JSON-LD hasVariant/offers, or Shopify variants."""
    found: list[str] = []

    # 0. Gap / Gap Factory / Old Navy / Banana Republic / Athleta buybox size labels
    for sz, disabled in re.findall(
        r'for=["\']pdp_buybox_dimension_([^"\']+)["\'][^>]{0,120}aria-disabled=["\'](true|false)["\']',
        page_html,
        re.IGNORECASE,
    ):
        if disabled.lower() == "false" and sz.strip():
            found.append(sz.strip())
    if found:
        return _dedupe_preserve_order(found)[:30]

    # 1. JSON-LD <script type="application/ld+json">
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page_html, re.S | re.I):
        try:
            obj = json.loads(block.strip())
        except Exception:
            continue
        nodes = obj if isinstance(obj, list) else [obj]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            variants = node.get("hasVariant") or node.get("offers") or []
            if isinstance(variants, dict):
                variants = [variants]
            for v in variants:
                if not isinstance(v, dict):
                    continue
                offers = v.get("offers")
                avail = str(v.get("availability") or (offers.get("availability") if isinstance(offers, dict) else "")).lower()
                if "instock" in avail or (avail == "" and v.get("size")):
                    sz = v.get("size")
                    if isinstance(sz, str) and 1 <= len(sz.strip()) <= 20:
                        found.append(sz.strip())

    if found:
        return _dedupe_preserve_order(found)

    # 2. Shopify / common inline JSON variants: {"option1":"L","available":true} or {"size":"M","inStock":true}
    for m in re.finditer(
        r'"(?:size|sizeName|option1)"\s*:\s*"([A-Za-z0-9./+\s-]{1,18})"[^\}]{0,120}"(?:available|inStock|isAvailable)"\s*:\s*true',
        page_html,
        re.IGNORECASE,
    ):
        val = m.group(1).strip()
        if re.match(r"^(?:XXS|XS|S|M|L|XL|XXL|2XL|3XL|4XL|\d{1,2}(?:\.\d)?|US\s*\d{1,2}(?:\.\d)?|EU\s*\d{2}(?:\.\d)?|M\s*\d.*|W\s*\d.*)$", val, re.IGNORECASE):
            found.append(val)

    return _dedupe_preserve_order(found)[:30]


def normalize_size_list(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [s.strip() for s in re.split(r"[,;/]+", raw) if s.strip()]
    return []


def parse_user_sizes_input(text: str) -> list[str]:
    cleaned = text.strip()
    if cleaned in ("-", "нет", "очистить", "clear", "none", "0"):
        return []
    parts = [p.strip() for p in re.split(r"[,;\n]+", cleaned) if p.strip()]
    return _dedupe_preserve_order(parts)[:15]


def size_matches_target(available_label: str, target: str) -> bool:
    """Match target like '10', '7.5', 'W 7.5', 'M', 'XL' against labels like 'M 10 / W 11.5', 'M 6 / W 7.5', 'L'."""
    avail_norm = re.sub(r"\s+", " ", available_label.strip().upper())
    targ_norm = re.sub(r"\s+", " ", target.strip().upper())
    if not targ_norm:
        return False
    if avail_norm == targ_norm:
        return True
    # Split slash-separated dual labels like "M 6 / W 7.5"
    segments = [seg.strip() for seg in avail_norm.split("/")]
    for seg in segments:
        if seg == targ_norm:
            return True
        # Match standalone number/letter token inside segment (so "10" matches "M 10" but NOT "M 10.5")
        if re.search(rf"(?<![A-Z0-9.]){re.escape(targ_norm)}(?![A-Z0-9.])", seg):
            # If user wrote just "M", don't match "M 10" (men's shoe prefix)
            if targ_norm in ("M", "W") and re.search(r"\d", seg):
                continue
            return True
    return False


def match_target_sizes(available_sizes: list[str], target_sizes: list[str]) -> list[str]:
    if not available_sizes or not target_sizes:
        return []
    matched = []
    for avail in available_sizes:
        if any(size_matches_target(avail, t) for t in target_sizes):
            matched.append(avail)
    return _dedupe_preserve_order(matched)


async def fetch_product_data(url: str) -> dict:
    """Fetch price, image, title, brand, stock status, and available sizes for a product URL."""
    price = DEFAULT_PRICE_PLACEHOLDER
    url_lower = url.lower()
    image_url: str | None = None
    if "levi.com" in url_lower:
        image_url = extract_levis_image(url)
    elif "thenorthface.com" in url_lower:
        image_url = extract_tnf_image(url)
    parsed_title: str | None = None
    parsed_brand: str | None = None
    in_stock: bool = True
    available_sizes: list[str] = []
    page_promo: dict | None = None

    try:
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome", timeout=15)
            if resp.status_code == 200:
                page_html = resp.text
                og_title = extract_meta_content(page_html, "og:title")
                if og_title:
                    parsed_title = og_title
                else:
                    title_match = re.search(r"<title[^>]*>([^<]+)</title>", page_html, re.IGNORECASE)
                    if title_match:
                        parsed_title = html.unescape(title_match.group(1)).strip()

                page_image = extract_page_image(page_html, url)
                if page_image:
                    image_url = page_image

                page_promo = extract_page_promo(page_html)
                if page_promo:
                    detected_brand = find_matched_brand(parsed_title) or find_matched_brand(url)
                    if detected_brand:
                        register_store_promo(
                            detected_brand,
                            page_promo["code"],
                            pct=page_promo.get("pct"),
                            source="сайт магазина",
                        )

                meta_price = (
                    extract_meta_content(page_html, "product:price:amount")
                    or extract_meta_content(page_html, "og:price:amount")
                )
                price_num = None
                if meta_price and re.match(r"^\d+(?:[.,]\d{1,2})?$", meta_price):
                    price_num = meta_price.replace(",", ".")
                else:
                    price_match = (
                        re.search(r'"price"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?', page_html, re.IGNORECASE)
                        or re.search(r'itemprop=["\']price["\'][^>]+content=["\'](\d+(?:\.\d{1,2})?)["\']', page_html, re.IGNORECASE)
                    )
                    if price_match:
                        price_num = price_match.group(1)

                if price_num:
                    symbol = detect_currency_symbol(page_html, url)
                    price = f"{symbol}{price_num}"

                if "nike.com" in url_lower:
                    nike_sizes, nike_in_stock = await fetch_nike_sizes(page_html, url, s)
                    if nike_in_stock is not None:
                        available_sizes = nike_sizes
                        in_stock = nike_in_stock
                elif "adidas." in url_lower:
                    ad_sizes, ad_in_stock = await fetch_adidas_sizes(url, s)
                    if ad_in_stock is not None:
                        available_sizes = ad_sizes
                        in_stock = ad_in_stock
                    else:
                        in_stock = not is_out_of_stock(page_html)
                        available_sizes = extract_generic_sizes(page_html)
                else:
                    in_stock = not is_out_of_stock(page_html)
                    available_sizes = extract_generic_sizes(page_html)
    except Exception as e:
        logger.warning("⚠️ Ошибка curl_cffi для %s: %s", url, e)

    needs_gas_sizes = (not available_sizes) and any(d in url_lower for d in ("levi.com", "calvinklein", "adidas.", "thenorthface.com"))
    if (price == DEFAULT_PRICE_PLACEHOLDER or not image_url or needs_gas_sizes) and GAS_PROXY_URL and http_session:
        try:
            async with http_session.get(
                GAS_PROXY_URL,
                params={"url": url},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success":
                        if price == DEFAULT_PRICE_PLACEHOLDER and data.get("price") and data.get("price") != "NOT_FOUND":
                            price = data.get("price")
                        if not image_url and data.get("image"):
                            image_url = normalize_image_url(data.get("image"), url)
                        if not parsed_title and data.get("title") and "error" not in data.get("title").lower():
                            parsed_title = data.get("title")
                        if not parsed_brand and data.get("brand"):
                            parsed_brand = data.get("brand")
                        if not available_sizes and data.get("sizes"):
                            available_sizes = normalize_size_list(data.get("sizes"))
                            if available_sizes:
                                in_stock = True
                        if "in_stock" in data and isinstance(data["in_stock"], bool):
                            in_stock = data["in_stock"]
                        if not page_promo and data.get("promo") and isinstance(data["promo"], dict) and data["promo"].get("code"):
                            page_promo = data["promo"]
        except Exception as e:
            logger.debug("GAS proxy fallback error for %s: %s", url, e)

    if parsed_title and any(w in parsed_title.lower() for w in ["oops", "error", "access denied", "not found", "forbidden", "just a moment", "attention required"]):
        parsed_title = None
        if not ("levi.com" in url_lower or "thenorthface.com" in url_lower):
            image_url = None

    return {
        "price": price,
        "image_url": image_url,
        "title": parsed_title,
        "brand": parsed_brand,
        "in_stock": in_stock,
        "available_sizes": available_sizes,
        "page_promo": page_promo,
    }


async def send_photo_or_text(
    chat_id: int | str,
    text: str,
    image_url: str | None = None,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
) -> bool:
    if image_url:
        caption = text if len(text) <= 1024 else text[:1020] + "..."
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=caption,
                reply_markup=reply_markup,
                disable_notification=disable_notification,
            )
            return True
        except Exception as e:
            logger.debug("Фото не отправилось (%s), шлю текстом: %s", image_url, e)

    safe_text = text if len(text) <= 4096 else text[:4092] + "..."
    await bot.send_message(
        chat_id=chat_id,
        text=safe_text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
        disable_notification=disable_notification,
    )
    return True


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить ссылку"), KeyboardButton(text="📋 Мой список")],
            [KeyboardButton(text="🔄 Проверить всё"), KeyboardButton(text="🔔 Алерты")],
            [KeyboardButton(text="🔍 Поиск"), KeyboardButton(text="💱 Курсы валют"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def extract_url_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"https?://[^\s<>\"']+", text.strip())
    return match.group(0).rstrip(".,);") if match else None


def format_sizes_block(avail_sizes: list[str]) -> str:
    if not avail_sizes:
        return ""
    joined = esc(", ".join(avail_sizes))
    if len(avail_sizes) > 5 or len(joined) > 45:
        return f"<blockquote expandable>👟 <b>Все размеры в наличии ({len(avail_sizes)}):</b>\n<code>{joined}</code></blockquote>"
    return f"👟 <b>Все размеры в наличии ({len(avail_sizes)}):</b> <code>{joined}</code>"


def format_tracked_item_card(item: dict, page_promo: dict | None = None) -> str:
    brand = item.get("brand") or "Одежда"
    title = item.get("title") or "Товар"
    last_price = item.get("last_price") or DEFAULT_PRICE_PLACEHOLDER
    min_price = item.get("min_price")
    in_stock = item.get("in_stock", True)
    avail_sizes = normalize_size_list(item.get("available_sizes"))
    target_sizes = normalize_size_list(item.get("target_sizes"))

    lines = [
        f"🏷 <b>Бренд:</b> {esc(brand)}",
        f"📦 <b>Модель:</b> {esc(title)}",
        f"💳 <b>Цена:</b> <code>{esc(format_price_multi(last_price))}</code>",
    ]
    active_promo = get_active_promo_for_item(item, page_promo=page_promo)
    lines.extend(format_promo_lines(last_price, active_promo))

    if min_price and min_price != DEFAULT_PRICE_PLACEHOLDER:
        lines.append(f"📉 <b>Минимум:</b> <code>{esc(min_price)}</code>")

    if in_stock is False:
        lines.append("🔴 <b>Наличие:</b> Нет в наличии (жду ресток)")
    elif avail_sizes:
        matched_targets = match_target_sizes(avail_sizes, target_sizes)
        if matched_targets:
            lines.append(f"⭐ <b>Твои размеры в наличии:</b> <code>{esc(', '.join(matched_targets))}</code>")
        elif target_sizes:
            lines.append(f"🎯 <b>Жду твои размеры:</b> <code>{esc(', '.join(target_sizes))}</code> (пока нет)")
        lines.append(format_sizes_block(avail_sizes))
    elif target_sizes:
        lines.append(f"🎯 <b>Отслеживаю размеры:</b> <code>{esc(', '.join(target_sizes))}</code>")

    return "\n".join(lines)


def build_tracked_item_kb(item: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛒 Перейти", url=item["url"]),
                InlineKeyboardButton(text="🔄 Проверить", callback_data=f"chk_{item['id']}"),
            ],
            [
                InlineKeyboardButton(text="👟 Размеры", callback_data=f"sz_{item['id']}"),
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{item['id']}"),
            ],
        ]
    )


async def add_url_to_tracker(message: types.Message, user_id: int, url: str) -> None:
    try:
        existing = await db_exec(
            lambda: supabase.table("tracked_items")
            .select("id")
            .eq("user_id", user_id)
            .eq("url", url)
            .execute()
        )
        if existing.data:
            await message.answer("ℹ️ Эта ссылка уже есть в твоём списке отслеживания.", reply_markup=get_main_keyboard())
            return
    except Exception as e:
        logger.error("DB duplicate check error: %s", e)

    status_msg = await message.answer("⏳ Сканирую товар (цена, фото, наличие и размеры)...")
    data = await fetch_product_data(url)
    price = data["price"]
    image_url = data["image_url"]
    title = clean_text(data["title"] or extract_title_from_url(url))
    brand = clean_text(data["brand"] or find_matched_brand(title) or find_matched_brand(url) or "Брендовая вещь")
    in_stock = data["in_stock"]
    avail_sizes = data["available_sizes"]
    min_price = price if price != DEFAULT_PRICE_PLACEHOLDER else None

    full_payload = {
        "user_id": user_id,
        "url": url,
        "brand": brand,
        "title": title,
        "last_price": price,
        "min_price": min_price,
        "image_url": image_url,
        "in_stock": in_stock,
        "available_sizes": avail_sizes,
        "target_sizes": [],
    }
    base_payload = {
        "user_id": user_id,
        "url": url,
        "brand": brand,
        "title": title,
        "last_price": price,
        "image_url": image_url,
    }

    try:
        try:
            res = await db_exec(lambda: supabase.table("tracked_items").insert(full_payload).execute())
        except Exception:
            res = await db_exec(lambda: supabase.table("tracked_items").insert(base_payload).execute())
        if not res.data:
            await message.answer("⚠️ Ошибка сохранения в базу.", reply_markup=get_main_keyboard())
            return
        saved_item = res.data[0]
        saved_item.setdefault("min_price", min_price)
        saved_item.setdefault("in_stock", in_stock)
        saved_item.setdefault("available_sizes", avail_sizes)
        saved_item.setdefault("target_sizes", [])
    except Exception as e:
        logger.error("DB insert error: %s", e)
        await message.answer("⚠️ Ошибка БД при сохранении.", reply_markup=get_main_keyboard())
        return

    try:
        await status_msg.delete()
    except Exception:
        pass

    caption = "✅ <b>Сохранено!</b>\n\n" + format_tracked_item_card(saved_item, page_promo=data.get("page_promo"))
    await send_photo_or_text(message.chat.id, caption, image_url=image_url, reply_markup=build_tracked_item_kb(saved_item))
    await message.answer(
        "Товар добавлен! Можешь нажать <b>👟 Размеры</b> под карточкой, чтобы задать приоритетные размеры для себя 👇",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = (message.text or "").split(maxsplit=1)
    if len(args) == 2 and args[1].startswith("track_"):
        deal_row_id = args[1].split("_", 1)[1].strip()
        try:
            res = await db_exec(lambda: supabase.table("posted_deals").select("*").eq("id", deal_row_id).limit(1).execute())
            if res.data and res.data[0].get("url"):
                await add_url_to_tracker(message, message.from_user.id, res.data[0]["url"])
                return
        except Exception as e:
            logger.warning("Deep-link track error: %s", e)
        await message.answer("⚠️ Не удалось найти ссылку для этого дропа.", reply_markup=get_main_keyboard())
        return

    await message.answer(
        "👋 <b>Привет! Я USA Drop Radar.</b>\n\n"
        "• Автоматически мониторю скидки и промокоды на брендовую одежду и обувь.\n"
        "• Отслеживаю <b>цены, минимум цены, наличие и размеры</b> по твоим ссылкам.\n"
        "• Умею присылать личные алерты по ключевым словам.\n\n"
        "Выбери действие в меню ниже 👇",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📖 <b>Команды бота:</b>\n\n"
        "• /track или <b>➕ Добавить ссылку</b> — добавить товар для отслеживания\n"
        "• /list или <b>📋 Мой список</b> — твои товары (цена, промокод, минимум, размеры)\n"
        "• /checkall или <b>🔄 Проверить всё</b> — проверить все товары сразу\n"
        "• /promo — сегодняшние промокоды (можно задать вручную: <code>/promo nike BFRIDAY 25</code>)\n"
        "• /checkmail — проверить новые письма с промокодами на почте\n"
        "• /stats — статистика канала, источников и бота\n"
        "• /digest — предпросмотр дайджеста «Топ-5 дропов дня» (или <code>/digest post</code> в канал)\n"
        "• /alert <code>слово</code> или <b>🔔 Алерты</b> — личные уведомления по ключевым словам\n"
        "• /search <code>nike</code> или <b>🔍 Поиск</b> — поиск по последним найденным дропам\n"
        "• /rates или <b>💱 Курсы валют</b> — актуальные курсы валют\n"
        "• /cancel — отменить текущее действие",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("promo"))
async def cmd_promo(message: types.Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split()
    if len(parts) == 1:
        now = time.time()
        active = [(b, p) for b, p in ACTIVE_STORE_PROMOS.items() if (now - p["ts"]) < PROMO_TTL_SECONDS]
        if not active:
            await message.answer(
                "🎟 <b>Активных промокодов пока нет.</b>\n"
                "Можешь добавить вручную: <code>/promo nike BFRIDAY 25</code>",
                reply_markup=get_main_keyboard(),
            )
            return
        lines = ["🎟 <b>Активные промокоды на сегодня:</b>\n"]
        for brand_key, p in sorted(active):
            disc = f" (-{p['pct']}%)" if p.get("pct") else ""
            lines.append(f"• <b>{esc(brand_key.title())}</b>: <code>{esc(p['code'])}</code>{disc}")
        lines.append("\n<i>Добавить/обновить:</i> <code>/promo nike BFRIDAY 25</code>\n<i>Удалить:</i> <code>/promo del nike</code>")
        await message.answer("\n".join(lines), reply_markup=get_main_keyboard())
        return

    if parts[1].lower() == "del" and len(parts) >= 3:
        brand_name = " ".join(parts[2:]).strip().lower()
        canonical = (find_matched_brand(brand_name) or brand_name).lower()
        removed = ACTIVE_STORE_PROMOS.pop(canonical, None) or ACTIVE_STORE_PROMOS.pop(brand_name, None)
        await delete_promo_from_db(canonical)
        if removed:
            await message.answer(f"🗑 Промокод для <b>{esc(brand_name.title())}</b> удалён.", reply_markup=get_main_keyboard())
        else:
            await message.answer("⚠️ Для этого бренда нет активного промокода.", reply_markup=get_main_keyboard())
        return

    if len(parts) >= 3:
        # Support multi-word brand like: /promo calvin klein SAVE20 20
        pct = None
        if parts[-1].rstrip("%").isdigit():
            pct = int(parts[-1].rstrip("%"))
            code = parts[-2].upper()
            brand_raw = " ".join(parts[1:-2]).strip()
        else:
            code = parts[-1].upper()
            brand_raw = " ".join(parts[1:-1]).strip()
        if not brand_raw or not code:
            await message.answer("⚠️ Формат: <code>/promo nike BFRIDAY 25</code>", reply_markup=get_main_keyboard())
            return
        canonical = find_matched_brand(brand_raw) or brand_raw.title()
        promo_obj = {
            "code": code,
            "pct": pct,
            "fixed": None,
            "fixed_cur": None,
            "source": "вручную",
            "ts": time.time(),
        }
        ACTIVE_STORE_PROMOS[canonical.lower()] = promo_obj
        _schedule_promo_persist(canonical.lower(), promo_obj)
        disc = f" (-{pct}%)" if pct else ""
        await message.answer(
            f"✅ Промокод сохранён: <b>{esc(canonical)}</b> → <code>{esc(code)}</code>{disc}\n"
            "Теперь он будет автоматически подставляться в <b>📋 Мой список</b>!",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer("⚠️ Формат: <code>/promo nike BFRIDAY 25</code> или <code>/promo del nike</code>", reply_markup=get_main_keyboard())


@dp.message(Command("rates"))
@dp.message(F.text == "💱 Курсы валют")
async def cmd_rates(message: types.Message):
    if not EXCHANGE_RATES:
        await load_exchange_rates()
    if not EXCHANGE_RATES:
        await message.answer("⚠️ Курсы валют сейчас недоступны, попробуй позже.")
        return
    uah_usd = EXCHANGE_RATES["UAH"]
    uah_eur = EXCHANGE_RATES["UAH"] / EXCHANGE_RATES["EUR"]
    uah_gbp = EXCHANGE_RATES["UAH"] / EXCHANGE_RATES["GBP"]
    await message.answer(
        "💱 <b>Актуальные курсы валют:</b>\n\n"
        f"🇺🇸 1 USD ≈ <b>{uah_usd:.2f} грн</b>\n"
        f"🇪🇺 1 EUR ≈ <b>{uah_eur:.2f} грн</b>\n"
        f"🇬🇧 1 GBP ≈ <b>{uah_gbp:.2f} грн</b>",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("cancel"))
@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👌 Действие отменено.", reply_markup=get_main_keyboard())


@dp.message(F.text == "➕ Добавить ссылку")
@dp.message(Command("track"))
async def start_tracking_flow(message: types.Message, state: FSMContext):
    await state.set_state(BotState.waiting_for_url)
    await message.answer(
        "🔗 <b>Отправь мне ссылку на вещь</b> (можно просто вставить сообщение со ссылкой):",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(BotState.waiting_for_url, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTONS))
async def process_track_url(message: types.Message, state: FSMContext):
    url = extract_url_from_text(message.text)
    if not url:
        await message.answer("⚠️ Не нашёл ссылку в сообщении. Отправь URL вида <code>https://...</code> или нажми <b>❌ Отмена</b>.")
        return
    await state.clear()
    await add_url_to_tracker(message, message.from_user.id, url)


@dp.message(F.text == "📋 Мой список")
@dp.message(Command("list"))
async def show_tracked_list(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        res = await db_exec(
            lambda: supabase.table("tracked_items")
            .select("*")
            .eq("user_id", message.from_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("DB list error: %s", e)
        await message.answer("⚠️ Ошибка загрузки списка.", reply_markup=get_main_keyboard())
        return

    if not res.data:
        await message.answer("📭 Список пуст. Нажми <b>➕ Добавить ссылку</b>, чтобы начать отслеживание.", reply_markup=get_main_keyboard())
        return

    await message.answer(f"📋 <b>Твои товары ({len(res.data)}):</b>", reply_markup=get_main_keyboard())
    for item in res.data:
        caption = format_tracked_item_card(item)
        await send_photo_or_text(
            message.chat.id,
            caption,
            image_url=item.get("image_url"),
            reply_markup=build_tracked_item_kb(item),
        )


@dp.callback_query(F.data.startswith("sz_"))
async def start_set_sizes(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split("_", 1)[1]
    await state.set_state(BotState.waiting_for_sizes)
    await state.update_data(size_item_id=item_id)
    await callback.answer()
    await callback.message.answer(
        "👟 <b>Введи свои приоритетные размеры через запятую</b>\n"
        "Примеры:\n"
        "• Обувь: <code>10, 7.5</code> или <code>M 10, W 7.5</code>\n"
        "• Одежда: <code>M, L</code>\n"
        "• Чтобы очистить приоритетные размеры, отправь <code>-</code>\n\n"
        "<i>Все остальные размеры и изменения цены всё равно будут отслеживаться и показываться!</i>",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(BotState.waiting_for_sizes, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTONS))
async def save_target_sizes(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    item_id = state_data.get("size_item_id")
    await state.clear()
    if not item_id:
        await message.answer("⚠️ Товар не выбран.", reply_markup=get_main_keyboard())
        return

    target_sizes = parse_user_sizes_input(message.text or "")
    try:
        res = await db_exec(
            lambda: supabase.table("tracked_items")
            .update({"target_sizes": target_sizes})
            .eq("id", item_id)
            .eq("user_id", message.from_user.id)
            .execute()
        )
        if not res.data:
            await message.answer("❌ Не удалось обновить размеры (проверь, выполнен ли SQL в Supabase).", reply_markup=get_main_keyboard())
            return
    except Exception as e:
        logger.error("Save target_sizes error: %s", e)
        await message.answer(
            "⚠️ Колонка <code>target_sizes</code> ещё не добавлена в Supabase. Выполни SQL-миграцию и попробуй снова.",
            reply_markup=get_main_keyboard(),
        )
        return

    if target_sizes:
        await message.answer(
            f"✅ Приоритетные размеры сохранены: <code>{esc(', '.join(target_sizes))}</code>\n"
            "Как только они появятся в наличии — пришлю приоритетный алерт 🎯",
            reply_markup=get_main_keyboard(),
        )
    else:
        await message.answer("✅ Приоритетные размеры очищены (буду показывать все размеры).", reply_markup=get_main_keyboard())


@dp.callback_query(F.data.startswith("del_"))
async def delete_tracked_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_", 1)[1]
    try:
        res = await db_exec(
            lambda: supabase.table("tracked_items")
            .delete()
            .eq("id", item_id)
            .eq("user_id", callback.from_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("DB delete error: %s", e)
        await callback.answer("⚠️ Ошибка удаления.", show_alert=True)
        return

    if not res.data:
        await callback.answer("❌ Товар не найден или нет прав.", show_alert=True)
        return
    await callback.answer("Удалено!")
    try:
        await callback.message.delete()
    except Exception:
        pass


def build_price_change_message(item: dict, new_price: str, min_price: str | None = None, avail_sizes: list[str] | None = None) -> str:
    old_price = item.get("last_price") or DEFAULT_PRICE_PLACEHOLDER
    old_amount, old_cur = parse_price_amount(old_price)
    new_amount, new_cur = parse_price_amount(new_price)

    header = "🔔 <b>ЦЕНА ИЗМЕНИЛАСЬ!</b>"
    if old_amount and new_amount and old_cur == new_cur:
        pct = ((new_amount - old_amount) / old_amount) * 100
        if new_amount < old_amount:
            header = f"📉 <b>ЦЕНА СНИЗИЛАСЬ ({pct:.0f}%)!</b>"
        elif new_amount > old_amount:
            header = f"📈 <b>ЦЕНА ВЫРОСЛА (+{pct:.0f}%)</b>"

    lines = [
        header,
        "",
        f"🏷 <b>{esc(item.get('brand') or 'Бренд')}</b>",
        f"📦 {esc(item.get('title') or 'Товар')}",
        f"💳 <b>Было:</b> <code>{esc(old_price)}</code>",
        f"💸 <b>Стало:</b> <code>{esc(format_price_multi(new_price))}</code>",
    ]
    if min_price and min_price != DEFAULT_PRICE_PLACEHOLDER:
        lines.append(f"📉 <b>Минимум:</b> <code>{esc(min_price)}</code>")

    target_sizes = normalize_size_list(item.get("target_sizes"))
    if avail_sizes:
        matched = match_target_sizes(avail_sizes, target_sizes)
        if matched:
            lines.append(f"⭐ <b>Твои размеры в наличии:</b> <code>{esc(', '.join(matched))}</code>")
        lines.append(format_sizes_block(avail_sizes))
    return "\n".join(lines)


async def evaluate_and_notify_item(item: dict, notify_if_unchanged: bool = False, chat_id: int | None = None) -> dict:
    """Check one tracked item for price, stock, and size updates, persist to DB, and notify user."""
    target_chat = chat_id or item["user_id"]
    data = await fetch_product_data(item["url"])
    new_price = data["price"]
    new_image = data["image_url"]
    new_in_stock = data["in_stock"]
    new_sizes = data["available_sizes"]

    old_price = item.get("last_price") or DEFAULT_PRICE_PLACEHOLDER
    old_min = item.get("min_price")
    old_in_stock = item.get("in_stock", True)
    old_sizes = normalize_size_list(item.get("available_sizes"))
    target_sizes = normalize_size_list(item.get("target_sizes"))

    image_to_use = item.get("image_url") or new_image
    updated_min = compute_min_price(old_min or old_price, new_price)

    update_fields: dict = {}
    if not item.get("image_url") and new_image:
        update_fields["image_url"] = new_image
    if updated_min and updated_min != old_min:
        update_fields["min_price"] = updated_min
    if new_in_stock != old_in_stock:
        update_fields["in_stock"] = new_in_stock
    if new_sizes != old_sizes and (new_sizes or old_sizes):
        update_fields["available_sizes"] = new_sizes

    # 1. Price change check (ALWAYS checked regardless of sizes)
    price_changed = False
    if new_price != DEFAULT_PRICE_PLACEHOLDER:
        old_amt, old_cur = parse_price_amount(old_price)
        new_amt, new_cur = parse_price_amount(new_price)
        is_same = (
            old_amt is not None
            and new_amt is not None
            and old_cur == new_cur
            and abs(old_amt - new_amt) < 0.01
        ) or new_price == old_price
        if not is_same:
            price_changed = True
            update_fields["last_price"] = new_price

    # 2. Size & stock restock check
    old_sizes_lower = {s.lower() for s in old_sizes}
    newly_added_sizes = [s for s in new_sizes if s.lower() not in old_sizes_lower]
    newly_added_targets = match_target_sizes(newly_added_sizes, target_sizes)
    back_in_stock = (old_in_stock is False) and (new_in_stock is True)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 КУПИТЬ", url=item["url"])]])
    notified = False

    if price_changed:
        msg = build_price_change_message(item, new_price, min_price=updated_min, avail_sizes=new_sizes)
        await send_photo_or_text(target_chat, msg, image_url=image_to_use, reply_markup=kb)
        notified = True

    if newly_added_targets:
        msg = (
            f"🎯 <b>ПОЯВИЛСЯ ТВОЙ РАЗМЕР!</b>\n\n"
            f"🏷 <b>{esc(item.get('brand') or 'Бренд')}</b>\n"
            f"📦 {esc(item.get('title') or 'Товар')}\n"
            f"⭐ <b>Появились твои размеры:</b> <code>{esc(', '.join(newly_added_targets))}</code>\n"
            f"💳 <b>Цена:</b> <code>{esc(format_price_multi(new_price if new_price != DEFAULT_PRICE_PLACEHOLDER else old_price))}</code>\n"
            f"{format_sizes_block(new_sizes)}"
        )
        await send_photo_or_text(target_chat, msg, image_url=image_to_use, reply_markup=kb)
        notified = True
    elif newly_added_sizes and (old_sizes or back_in_stock):
        added_preview = ", ".join(newly_added_sizes[:12]) + (" ..." if len(newly_added_sizes) > 12 else "")
        msg = (
            f"🟢 <b>ПОПОЛНЕНИЕ РАЗМЕРОВ!</b>\n\n"
            f"🏷 <b>{esc(item.get('brand') or 'Бренд')}</b>\n"
            f"📦 {esc(item.get('title') or 'Товар')}\n"
            f"➕ <b>Новые размеры:</b> <code>{esc(added_preview)}</code>\n"
            f"💳 <b>Цена:</b> <code>{esc(format_price_multi(new_price if new_price != DEFAULT_PRICE_PLACEHOLDER else old_price))}</code>\n"
            f"{format_sizes_block(new_sizes)}"
        )
        await send_photo_or_text(target_chat, msg, image_url=image_to_use, reply_markup=kb)
        notified = True
    elif back_in_stock:
        msg = (
            f"🟢 <b>СНОВА В НАЛИЧИИ!</b>\n\n"
            f"🏷 <b>{esc(item.get('brand') or 'Бренд')}</b>\n"
            f"📦 {esc(item.get('title') or 'Товар')}\n"
            f"💳 <b>Цена:</b> <code>{esc(format_price_multi(new_price if new_price != DEFAULT_PRICE_PLACEHOLDER else old_price))}</code>"
        )
        await send_photo_or_text(target_chat, msg, image_url=image_to_use, reply_markup=kb)
        notified = True

    if update_fields:
        await safe_update_tracked_item(item["id"], update_fields)

    if notify_if_unchanged and not notified:
        if new_price == DEFAULT_PRICE_PLACEHOLDER and not new_sizes:
            await bot.send_message(
                target_chat,
                f"⚠️ Не удалось получить актуальные данные для <b>{esc(item.get('title'))}</b>.",
            )
        else:
            effective_price = new_price if new_price != DEFAULT_PRICE_PLACEHOLDER else old_price
            lines = [
                f"✅ <b>{esc(item.get('title'))}</b> — цена без изменений: <code>{esc(format_price_multi(effective_price))}</code>"
            ]
            active_promo = get_active_promo_for_item(item, page_promo=data.get("page_promo"))
            lines.extend(format_promo_lines(effective_price, active_promo))
            if new_in_stock is False:
                lines.append("🔴 Сейчас нет в наличии.")
            elif new_sizes:
                matched = match_target_sizes(new_sizes, target_sizes)
                if matched:
                    lines.append(f"⭐ Твои размеры в наличии: <code>{esc(', '.join(matched))}</code>")
                elif target_sizes:
                    lines.append(f"🎯 Твои размеры (<code>{esc(', '.join(target_sizes))}</code>) пока отсутствуют.")
                lines.append(format_sizes_block(new_sizes))
            await bot.send_message(target_chat, "\n".join(lines))

    return {
        "price_changed": price_changed,
        "restocked": bool(newly_added_targets or newly_added_sizes or back_in_stock),
        "target_hit": bool(newly_added_targets),
    }


@dp.callback_query(F.data.startswith("chk_"))
async def manual_check_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_", 1)[1]
    await callback.answer("⏳ Проверяю цену и размеры...")
    try:
        res = await db_exec(
            lambda: supabase.table("tracked_items")
            .select("*")
            .eq("id", item_id)
            .eq("user_id", callback.from_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("DB manual check error: %s", e)
        return

    if not res.data:
        await callback.message.answer("❌ Товар не найден.")
        return

    await evaluate_and_notify_item(res.data[0], notify_if_unchanged=True, chat_id=callback.from_user.id)


@dp.message(F.text == "🔄 Проверить всё")
@dp.message(Command("checkall"))
async def check_all_user_items(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        res = await db_exec(
            lambda: supabase.table("tracked_items")
            .select("*")
            .eq("user_id", message.from_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("DB checkall error: %s", e)
        await message.answer("⚠️ Ошибка загрузки списка.", reply_markup=get_main_keyboard())
        return

    if not res.data:
        await message.answer("📭 Список пуст.", reply_markup=get_main_keyboard())
        return

    status_msg = await message.answer(f"⏳ Проверяю все твои товары ({len(res.data)} шт.)...")
    changed_prices = 0
    restocks = 0
    for item in res.data:
        try:
            outcome = await evaluate_and_notify_item(item, notify_if_unchanged=False, chat_id=message.from_user.id)
            if outcome["price_changed"]:
                changed_prices += 1
            if outcome["restocked"]:
                restocks += 1
        except Exception as e:
            logger.warning("checkall item %s error: %s", item.get("id"), e)
        await asyncio.sleep(0.5)

    try:
        await status_msg.delete()
    except Exception:
        pass

    await message.answer(
        f"✅ <b>Проверка завершена!</b>\n\n"
        f"• Проверено товаров: <b>{len(res.data)}</b>\n"
        f"• Изменений цены: <b>{changed_prices}</b>\n"
        f"• Пополнений / рестоков: <b>{restocks}</b>",
        reply_markup=get_main_keyboard(),
    )


# --- Personal Keyword Alerts (/alert) ---

@dp.message(F.text == "🔔 Алерты")
@dp.message(Command("alert"))
async def cmd_alerts(message: types.Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if (message.text or "").startswith("/alert") and len(parts) == 2 and parts[1].strip():
        await add_user_alert(message, parts[1].strip())
        return

    try:
        res = await db_exec(
            lambda: supabase.table("user_alerts")
            .select("*")
            .eq("user_id", message.from_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("DB user_alerts error: %s", e)
        await message.answer(
            "⚠️ Таблица <code>user_alerts</code> ещё не создана в Supabase. Выполни SQL-миграцию из <code>supabase_migration.sql</code>.",
            reply_markup=get_main_keyboard(),
        )
        return

    rows = res.data or []
    buttons = [[InlineKeyboardButton(text="➕ Добавить слово", callback_data="add_alert")]]
    for row in rows:
        buttons.append([InlineKeyboardButton(text=f"❌ {row['keyword']}", callback_data=f"delalert_{row['id']}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if rows:
        kw_list = ", ".join(f"<code>{esc(r['keyword'])}</code>" for r in rows)
        text = f"🔔 <b>Твои личные алерты ({len(rows)}):</b>\n{kw_list}\n\nКак только в RSS появится дроп с этим словом — сразу пришлю в ЛС."
    else:
        text = "🔔 <b>У тебя пока нет личных алертов.</b>\nНажми <b>➕ Добавить слово</b> или напиши <code>/alert salomon</code>."
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "add_alert")
async def start_add_alert(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BotState.waiting_for_alert_keyword)
    await callback.answer()
    await callback.message.answer(
        "🔔 <b>Введи ключевое слово или модель</b> (например: <code>p-6000</code>, <code>salomon</code>, <code>puffer</code>):",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(BotState.waiting_for_alert_keyword, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTONS))
async def process_add_alert(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user_alert(message, (message.text or "").strip())


async def add_user_alert(message: types.Message, keyword: str) -> None:
    kw = clean_text(keyword).lower()
    if len(kw) < 2:
        await message.answer("⚠️ Слово слишком короткое.", reply_markup=get_main_keyboard())
        return
    try:
        await db_exec(
            lambda: supabase.table("user_alerts")
            .upsert({"user_id": message.from_user.id, "keyword": kw}, on_conflict="user_id,keyword")
            .execute()
        )
        await message.answer(
            f"✅ Алерт <code>{esc(kw)}</code> добавлен! Как только появится дроп с этим словом — пришлю сюда.",
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        logger.error("DB insert alert error: %s", e)
        await message.answer(
            "⚠️ Не удалось сохранить алерт (убедись, что таблица <code>user_alerts</code> создана в Supabase).",
            reply_markup=get_main_keyboard(),
        )


@dp.callback_query(F.data.startswith("delalert_"))
async def delete_user_alert(callback: types.CallbackQuery):
    alert_id = callback.data.split("_", 1)[1]
    try:
        await db_exec(
            lambda: supabase.table("user_alerts")
            .delete()
            .eq("id", alert_id)
            .eq("user_id", callback.from_user.id)
            .execute()
        )
        await callback.answer("Алерт удалён!")
        await callback.message.answer("🗑 Алерт удалён. Нажми <b>🔔 Алерты</b>, чтобы посмотреть обновлённый список.")
    except Exception as e:
        logger.error("DB delete alert error: %s", e)
        await callback.answer("⚠️ Ошибка удаления.", show_alert=True)


# --- Search Posted Deals (/search) ---

@dp.message(F.text == "🔍 Поиск")
@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if (message.text or "").startswith("/search") and len(parts) == 2 and parts[1].strip():
        await run_deal_search(message, parts[1].strip())
        return
    await state.set_state(BotState.waiting_for_search_query)
    await message.answer(
        "🔍 <b>Что ищем среди последних дропов?</b>\nВведи бренд или модель (например: <code>nike</code>, <code>hoodie</code>, <code>salomon</code>):",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(BotState.waiting_for_search_query, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTONS))
async def process_search_query(message: types.Message, state: FSMContext):
    await state.clear()
    await run_deal_search(message, (message.text or "").strip())


async def run_deal_search(message: types.Message, query: str) -> None:
    q = clean_text(query)
    if len(q) < 2:
        await message.answer("⚠️ Запрос слишком короткий.", reply_markup=get_main_keyboard())
        return
    try:
        res = await db_exec(
            lambda: supabase.table("posted_deals")
            .select("*")
            .or_(f"title.ilike.%{q}%,brand.ilike.%{q}%")
            .order("id", desc=True)
            .limit(8)
            .execute()
        )
    except Exception as e:
        logger.error("DB search error: %s", e)
        await message.answer("⚠️ Ошибка поиска в базе.", reply_markup=get_main_keyboard())
        return

    rows = res.data or []
    if not rows:
        await message.answer(f"🔎 По запросу <code>{esc(q)}</code> пока ничего не найдено.", reply_markup=get_main_keyboard())
        return

    lines = [f"🔍 <b>Последние дропы по запросу «{esc(q)}» ({len(rows)}):</b>\n"]
    for idx, row in enumerate(rows, 1):
        title = esc(row.get("title") or "Дроп")
        brand = esc(row.get("brand") or "")
        price = row.get("price")
        url = row.get("url")
        price_part = f" — <code>{esc(price)}</code>" if price else ""
        if url:
            lines.append(f"{idx}. <b>[{brand}]</b> <a href=\"{html.escape(url)}\">{title}</a>{price_part}")
        else:
            lines.append(f"{idx}. <b>[{brand}]</b> {title}{price_part}")

    await message.answer("\n".join(lines), disable_web_page_preview=True, reply_markup=get_main_keyboard())


# --- Background Loops ---

async def check_personal_tracker():
    while True:
        try:
            res = await db_exec(lambda: supabase.table("tracked_items").select("*").execute())
            if res.data:
                logger.info("🔍 Проверка персонального трекера: %d товаров", len(res.data))
                for item in res.data:
                    try:
                        await evaluate_and_notify_item(item, notify_if_unchanged=False)
                    except Exception as e:
                        logger.warning("⚠️ Ошибка проверки товара %s: %s", item.get("id"), e)
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error("⚠️ Ошибка персонального трекера: %s", e)
        await asyncio.sleep(TRACKER_INTERVAL)


def normalize_deal_title(title: str) -> str:
    """Strip prices, sizes, promo/shipping tails, and bracket tags to isolate the core product name."""
    t = clean_text(title).lower()
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)
    t = re.sub(r"[$£€]\s*\d+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?\s*[$£€]", " ", t)
    t = re.sub(r"\b\d{1,2}\s*%\s*(?:off|rabatt)?\b", " ", t)
    t = re.sub(r"\b(?:free shipping|free pickup|ohne cb|mit code|w/\s*code|plus shipping|fs on)\b.*$", " ", t)
    t = re.sub(r"\b(?:gr[oö][sß]e|size|sizes)\b[^|•,-]*", " ", t)
    t = re.sub(r"\b\d{2}(?:\.\d)?\s*[-–]\s*\d{2}(?:\.\d)?\b", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_url_for_dedup(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"


def is_similar_product(title_a: str, title_b: str) -> bool:
    na = normalize_deal_title(title_a)
    nb = normalize_deal_title(title_b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if SequenceMatcher(None, na, nb).ratio() >= 0.78:
        return True
    toks_a = {w for w in na.split() if len(w) > 1}
    toks_b = {w for w in nb.split() if len(w) > 1}
    if len(toks_a) >= 3 and len(toks_b) >= 3:
        overlap = len(toks_a & toks_b) / max(len(toks_a), len(toks_b))
        if overlap >= 0.72:
            return True
    return False


def is_price_drop(old_price_str: str | None, new_price_str: str | None) -> bool:
    """Return True if new_price is strictly lower than old_price in the same currency (by > 0.50)."""
    old_amt, old_cur = parse_price_amount(old_price_str)
    new_amt, new_cur = parse_price_amount(new_price_str)
    if old_amt is not None and new_amt is not None and old_cur == new_cur:
        return new_amt < (old_amt - 0.5)
    return False


async def is_deal_already_posted(deal_id: str, title: str, brand: str = "", target_link: str = "", new_price: str = "") -> tuple[bool, str | None]:
    """Return (is_duplicate, previous_price_if_dropped). Blocks same product for DEAL_DEDUP_DAYS unless price dropped."""
    if deal_id in RECENT_DEAL_IDS_SET or title in RECENT_TITLES_SET:
        return True, None
    try:
        res_id = await db_exec(
            lambda: supabase.table("posted_deals").select("id").eq("deal_id", deal_id).limit(1).execute()
        )
        if res_id.data:
            remember_deal(deal_id, title)
            return True, None

        since_iso = (datetime.now(timezone.utc) - timedelta(days=DEAL_DEDUP_DAYS)).isoformat()
        res_recent = await db_exec(
            lambda: supabase.table("posted_deals")
            .select("title,url,price")
            .eq("brand", brand)
            .gte("created_at", since_iso)
            .order("id", desc=True)
            .limit(150)
            .execute()
        )
        norm_target = normalize_url_for_dedup(target_link)
        for row in res_recent.data or []:
            same_url = bool(norm_target and row.get("url") and normalize_url_for_dedup(row["url"]) == norm_target)
            same_model = bool(row.get("title") and is_similar_product(title, row["title"]))
            if same_url or same_model:
                prev_price = row.get("price")
                if is_price_drop(prev_price, new_price):
                    return False, prev_price
                remember_deal(deal_id, title)
                return True, None
    except Exception as e:
        logger.warning("⚠️ Ошибка проверки дубликата в БД: %s", e)
    return False, None


async def notify_matching_user_alerts(title: str, brand: str, post_text: str, image_url: str | None, kb: InlineKeyboardMarkup) -> None:
    try:
        res = await db_exec(lambda: supabase.table("user_alerts").select("user_id,keyword").execute())
    except Exception:
        return
    if not res.data:
        return

    haystack = f"{title} {brand}".lower()
    notified_users: set[int] = set()
    for row in res.data:
        uid = row["user_id"]
        kw = (row.get("keyword") or "").lower().strip()
        if not kw or uid in notified_users:
            continue
        if kw in haystack:
            notified_users.add(uid)
            alert_text = f"🔔 <b>Сработал твой алерт:</b> <code>{esc(kw)}</code>\n\n{post_text}"
            try:
                await send_photo_or_text(uid, alert_text, image_url=image_url, reply_markup=kb, disable_web_page_preview=False)
            except Exception as e:
                logger.debug("Alert DM error for %s: %s", uid, e)


async def notify_tracked_users_about_promo(brand: str, promo: dict) -> None:
    """When a new promo code for a brand appears today, alert users tracking items of that brand with exact discounted prices."""
    try:
        res = await db_exec(lambda: supabase.table("tracked_items").select("*").execute())
    except Exception:
        return
    if not res.data:
        return

    brand_lower = brand.strip().lower()
    by_user: dict[int, list[dict]] = {}
    for item in res.data:
        item_brand = (item.get("brand") or "").strip().lower()
        url_brand = (find_matched_brand(item.get("url")) or "").strip().lower()
        if item_brand == brand_lower or url_brand == brand_lower:
            by_user.setdefault(item["user_id"], []).append(item)

    code = promo["code"]
    disc_desc = ""
    if promo.get("pct"):
        disc_desc = f" (-{promo['pct']}%)"
    elif promo.get("fixed") and promo.get("fixed_cur"):
        sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(promo["fixed_cur"], "$")
        disc_desc = f" (-{sym}{promo['fixed']:.0f})"

    for uid, items in by_user.items():
        dedup_key = (uid, brand_lower, code)
        if dedup_key in NOTIFIED_PROMOS:
            continue
        NOTIFIED_PROMOS.add(dedup_key)

        lines = [
            f"🎟 <b>ПРОМОКОД СЕГОДНЯ НА {esc(brand.upper())}!</b>",
            f"Код: <code>{esc(code)}</code>{disc_desc}\n",
            "<b>Твои товары из списка с этим промокодом:</b>",
        ]
        for item in items[:10]:
            title = esc(item.get("title") or "Товар")
            url = item.get("url") or ""
            base_price = item.get("last_price") or DEFAULT_PRICE_PLACEHOLDER
            disc_price = apply_promo_to_price(base_price, promo)
            if disc_price:
                lines.append(f"• <a href=\"{html.escape(url)}\">{title}</a>: <code>{esc(base_price)}</code> ➡️ <b>{esc(disc_price)}</b>")
            else:
                lines.append(f"• <a href=\"{html.escape(url)}\">{title}</a>: <code>{esc(base_price)}</code>")
        try:
            await bot.send_message(uid, "\n".join(lines), disable_web_page_preview=True)
        except Exception as e:
            logger.debug("Promo DM error for %s: %s", uid, e)


async def process_entry(entry, flag: str):
    title = clean_text(getattr(entry, "title", ""))
    main_link = getattr(entry, "link", "")
    summary = getattr(entry, "summary", "")
    if not title or not main_link:
        return

    brand = find_matched_brand(title)
    if not brand:
        return

    discount_pct = extract_discount_pct(title, summary)
    fixed_disc, fixed_cur = extract_discount_fixed(title, summary)
    promo_code = extract_promo_code(title, summary)

    # Always harvest today's promo codes from RSS feeds even if the deal was already posted before restart
    is_new_promo = False
    if promo_code:
        is_new_promo = register_store_promo(
            brand,
            promo_code,
            pct=discount_pct,
            fixed=fixed_disc,
            fixed_cur=fixed_cur,
            source="RSS",
        )

    deal_id = getattr(entry, "id", main_link)
    target_link = extract_best_link(summary, main_link)
    price = extract_price(title, summary)
    already_posted, prev_price = await is_deal_already_posted(
        deal_id, title, brand=brand, target_link=target_link, new_price=price
    )
    if already_posted:
        return

    if is_new_promo and promo_code:
        promo_obj = ACTIVE_STORE_PROMOS.get(brand.strip().lower())
        if promo_obj:
            await notify_tracked_users_about_promo(brand, promo_obj)

    has_numeric_price = parse_price_amount(price)[0] is not None
    compare_pct = extract_discount_from_compare_price(title, summary, price)
    effective_discount = max([p for p in (discount_pct, compare_pct) if p is not None], default=None)

    # Quality gate (35%+ discount, or concrete price <= $95 when discount % isn't mentioned)
    if effective_discount is not None and effective_discount < MIN_DISCOUNT_PCT:
        return
    if effective_discount is None:
        if not has_numeric_price:
            return
        usd_val = price_to_usd(price)
        if usd_val is not None and usd_val > MAX_PRICE_NO_DISCOUNT_USD:
            return

    image_url = extract_hd_image(entry, summary)

    try:
        async with AsyncSession() as s:
            resp = await s.get(target_link, impersonate="chrome", timeout=10)
            if resp.status_code == 200:
                page_html = resp.text
                if is_out_of_stock(page_html):
                    return
                og_img = extract_page_image(page_html, target_link)
                if og_img and not any(bw in og_img.lower() for bw in ["sale", "banner", "promo", "off"]):
                    image_url = og_img
    except Exception:
        pass

    # Insert into DB first to get row_id for deep-link "➕ Отслеживать" button
    deal_row_id = None
    try:
        try:
            ins = await db_exec(
                lambda: supabase.table("posted_deals")
                .insert({
                    "deal_id": deal_id,
                    "title": title,
                    "brand": brand,
                    "price": price,
                    "url": target_link,
                    "discount_pct": effective_discount,
                })
                .execute()
            )
        except Exception:
            try:
                ins = await db_exec(
                    lambda: supabase.table("posted_deals")
                    .insert({"deal_id": deal_id, "title": title, "brand": brand, "price": price, "url": target_link})
                    .execute()
                )
            except Exception:
                ins = await db_exec(
                    lambda: supabase.table("posted_deals")
                    .insert({"deal_id": deal_id, "title": title, "brand": brand})
                    .execute()
                )
        if ins.data:
            deal_row_id = ins.data[0].get("id")
    except Exception as e:
        logger.error("⚠️ Ошибка сохранения posted_deals: %s", e)

    price_with_multi = format_price_multi(price)
    top_badge = f"🔥🔥🔥 <b>ТОП ДРОП (-{effective_discount}%)</b>\n" if (effective_discount and effective_discount >= 50) else ""
    disc_badge = f"🔥 <b>Скидка ~{effective_discount}%</b>\n" if (effective_discount and MIN_DISCOUNT_PCT <= effective_discount < 50) else ""
    drop_note = f"📉 <b>ЦЕНА СТАЛА ЕЩЁ НИЖЕ (было {esc(prev_price)})!</b>\n" if prev_price else ""
    promo_line = f"\n🎟 <b>Промокод:</b> <code>{esc(promo_code)}</code>" if promo_code else ""

    post_text = (
        f"{drop_note}"
        f"{top_badge or disc_badge}"
        f"<b>DROP SIGNAL | {brand_to_hashtag(brand)}</b>\n"
        f"{esc(flag)}\n\n"
        f"<b>{esc(title)}</b>\n"
        f"💳 <b>Цена:</b> {esc(price_with_multi)}"
        f"{promo_line}"
    )

    buttons = [[InlineKeyboardButton(text="🛒 Купить (1 клик)", url=target_link)]]
    second_row = []
    if main_link != target_link:
        second_row.append(InlineKeyboardButton(text="🔗 Источник", url=main_link))
    if BOT_USERNAME and deal_row_id:
        second_row.append(
            InlineKeyboardButton(text="➕ Отслеживать", url=f"https://t.me/{BOT_USERNAME}?start=track_{deal_row_id}")
        )
    if second_row:
        buttons.append(second_row)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await send_photo_or_text(
            CHANNEL_ID,
            post_text,
            image_url=image_url,
            reply_markup=kb,
            disable_web_page_preview=False,
            disable_notification=is_quiet_hours(),
        )
        remember_deal(deal_id, title)
        logger.info("✅ Опубликован дроп: [%s] %s", brand, title[:80])
        await notify_matching_user_alerts(title, brand, post_text, image_url, kb)
        await asyncio.sleep(3)
    except Exception as e:
        logger.error("⚠️ Ошибка публикации поста: %s", e)


async def fetch_feed(source: dict):
    url = source["url"]
    flag = source["flag"]
    raw_xml = ""
    try:
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome", timeout=15)
            if resp.status_code == 200 and resp.text:
                raw_xml = resp.text
            elif resp.status_code in (403, 429) and GAS_PROXY_URL and http_session:
                async with http_session.get(
                    GAS_PROXY_URL,
                    params={"url": url, "raw": "1"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as g_resp:
                    if g_resp.status == 200:
                        raw_xml = await g_resp.text()
            else:
                logger.debug("RSS %s вернул статус %d", url, resp.status_code)
    except Exception as e:
        logger.debug("Ошибка загрузки RSS %s: %s", url, e)

    if not raw_xml:
        return [], flag
    try:
        feed = await asyncio.to_thread(feedparser.parse, raw_xml)
        return feed.entries, flag
    except Exception as e:
        logger.debug("Ошибка парсинга RSS %s: %s", url, e)
        return [], flag


async def scan_and_post(sources: list[dict]):
    sem = asyncio.Semaphore(6)

    async def _bounded_fetch(source: dict):
        async with sem:
            return await fetch_feed(source)

    results = await asyncio.gather(*[_bounded_fetch(source) for source in sources])
    for entries, flag in results:
        for entry in entries:
            await process_entry(entry, flag)


async def run_radar_standard():
    while True:
        try:
            await scan_and_post(STANDARD_RSS_SOURCES)
        except Exception as e:
            logger.error("⚠️ Ошибка стандартного радара: %s", e)
        await asyncio.sleep(RADAR_STANDARD_INTERVAL)


async def run_radar_reddit():
    while True:
        try:
            await scan_and_post(REDDIT_RSS_SOURCES)
        except Exception as e:
            logger.error("⚠️ Ошибка Reddit радара: %s", e)
        await asyncio.sleep(RADAR_REDDIT_INTERVAL)


def decode_mime_str(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(str(chunk))
    return clean_text("".join(parts))


def extract_email_body(msg: Message) -> tuple[str, str]:
    """Return (plain_text, raw_html) from an email message."""
    plain_parts, html_parts = [], []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="ignore")
            if ctype == "text/plain":
                plain_parts.append(decoded)
            elif ctype == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="ignore")
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    raw_html = "\n".join(html_parts)
    plain = "\n".join(plain_parts)
    html_text = ""
    if raw_html:
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
        html_text = clean_text(re.sub(r"<[^>]+>", " ", stripped))
    combined_text = f"{plain}\n{html_text}".strip()
    return combined_text, raw_html


def extract_email_store_link(raw_html: str) -> str | None:
    if not raw_html:
        return None
    skip_kws = ("unsubscribe", "optout", "privacy", "manage", "preferences", "facebook.com", "instagram.com", "twitter.com", "tiktok.com", "youtube.com", "mailto:", "view-in-browser")
    for url in re.findall(r'href=["\'](https?://[^"\']+)["\']', raw_html, re.IGNORECASE):
        low = url.lower()
        if any(sk in low for sk in skip_kws):
            continue
        if len(url) < 500:
            return html.unescape(url)
    return None


def extract_promo_validity(subject: str, body: str) -> str | None:
    """Extract promo validity dates like '12/2-12/7', 'Ends Nov 30', 'Valid through 12/7', 'Expires 11/29/26'."""
    text = f"{subject} {body[:4000]}"
    months = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    date_tok = rf"(?:\d{{1,2}}[/.]\d{{1,2}}(?:[/.]\d{{2,4}})?|{months}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?)"

    # 1. Explicit date range like "12/2-12/7" or "Nov 25 - Dec 2"
    m_range = re.search(rf"\b({date_tok}\s*[-–—]\s*{date_tok})\b", text, re.IGNORECASE)
    if m_range:
        return clean_text(m_range.group(1))

    # 2. Explicit expiration phrases like "Ends 11/30", "Valid through Dec 7", "Expires 12/7"
    m_end = re.search(
        rf"\b(?:ends?|offer\s+ends?|valid\s+(?:through|thru|until)|expires?(?:\s+on)?|redeem\s+by|действует\s+до|до)\s*[:\-]?\s*({date_tok})\b",
        text,
        re.IGNORECASE,
    )
    if m_end:
        return f"до {clean_text(m_end.group(1))}"

    return None


def fetch_unread_emails_sync(limit: int = 10) -> list[dict]:
    """Connect to IMAP (ukr.net), read up to `limit` UNSEEN messages, mark them Seen, and return parsed dicts."""
    if not EMAIL_USER or not EMAIL_PASS:
        return []
    results = []
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []
        msg_ids = data[0].split()[-limit:]
        for mid in msg_ids:
            try:
                st, msg_data = mail.fetch(mid, "(RFC822)")
                if st != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject = decode_mime_str(msg.get("Subject"))
                sender = decode_mime_str(msg.get("From"))
                plain, raw_html = extract_email_body(msg)
                mail.store(mid, "+FLAGS", "\\Seen")

                combined_check = f"{subject} {plain[:600]}".lower()
                if any(sw in combined_check for sw in EMAIL_SERVICE_STOP_WORDS):
                    continue

                # Strip Fwd:/Re: prefix for cleaner display
                clean_subj = re.sub(r"^(?:(?:fwd?|re)\s*:\s*)+", "", subject, flags=re.IGNORECASE).strip() or "Промо от магазина"
                brand = (
                    find_matched_brand(clean_subj)
                    or find_matched_brand(sender)
                    or find_matched_brand(plain[:1500])
                    or "Магазин"
                )
                promo_code = extract_promo_code(clean_subj, plain[:8000])
                if not promo_code:
                    continue
                discount_pct = extract_discount_pct(clean_subj, plain[:8000])
                fixed_disc, fixed_cur = extract_discount_fixed(clean_subj, plain[:8000])
                valid_until = extract_promo_validity(clean_subj, plain[:8000])
                link = extract_email_store_link(raw_html)

                results.append({
                    "subject": clean_subj,
                    "sender": sender,
                    "brand": brand,
                    "promo_code": promo_code,
                    "discount_pct": discount_pct,
                    "fixed_disc": fixed_disc,
                    "fixed_cur": fixed_cur,
                    "valid_until": valid_until,
                    "link": link,
                })
            except Exception as e:
                logger.warning("⚠️ Ошибка разбора письма %s: %s", mid, e)
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return results


async def resolve_owner_user_id() -> int | None:
    if ADMIN_USER_ID:
        return ADMIN_USER_ID
    try:
        res = await db_exec(lambda: supabase.table("tracked_items").select("user_id").limit(1).execute())
        if res.data:
            return int(res.data[0]["user_id"])
    except Exception:
        pass
    return None


async def process_email_promos(target_user_id: int | None = None) -> int:
    global EMAIL_PROMO_SEQ
    if not EMAIL_USER or not EMAIL_PASS:
        return 0
    owner_id = target_user_id or await resolve_owner_user_id()
    if not owner_id:
        return 0

    emails = await asyncio.to_thread(fetch_unread_emails_sync)
    if not emails:
        return 0

    tracked_res = await db_exec(lambda: supabase.table("tracked_items").select("*").eq("user_id", owner_id).execute())
    tracked_items = tracked_res.data or []

    sent_count = 0
    for em in emails:
        EMAIL_PROMO_SEQ += 1
        key = str(EMAIL_PROMO_SEQ)
        if len(PENDING_EMAIL_PROMOS) > 200:
            oldest_key = next(iter(PENDING_EMAIL_PROMOS))
            PENDING_EMAIL_PROMOS.pop(oldest_key, None)
        PENDING_EMAIL_PROMOS[key] = em

        brand = em["brand"]
        promo_code = em["promo_code"]
        pct = em["discount_pct"]
        fixed = em["fixed_disc"]
        fixed_cur = em["fixed_cur"]

        disc_desc = ""
        if pct:
            disc_desc = f" (-{pct}%)"
        elif fixed and fixed_cur:
            sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(fixed_cur, "$")
            disc_desc = f" (-{sym}{fixed:.0f})"

        lines = [
            f"📩 <b>ПИСЬМО ОТ МАГАЗИНА | {esc(brand)}</b>",
            f"<b>{esc(em['subject'])}</b>",
        ]
        if promo_code:
            lines.append(f"\n🎟 <b>Промокод:</b> <code>{esc(promo_code)}</code>{disc_desc}")
        elif disc_desc:
            lines.append(f"\n🔥 <b>Скидка в письме:</b> <b>{disc_desc.strip(' ()')}</b>")
        if em.get("valid_until"):
            lines.append(f"⏳ <b>Действует:</b> <code>{esc(em['valid_until'])}</code>")

        # Show discounted prices for matching items in user's tracked list
        promo_obj = {"code": promo_code, "pct": pct, "fixed": fixed, "fixed_cur": fixed_cur} if (promo_code or pct or fixed) else None
        brand_low = brand.lower()
        matching_items = [
            it for it in tracked_items
            if (it.get("brand") or "").strip().lower() == brand_low
            or (find_matched_brand(it.get("url")) or "").strip().lower() == brand_low
        ]
        if matching_items and promo_obj and (pct or fixed):
            lines.append("\n<b>Твои товары из списка с этой скидкой:</b>")
            for it in matching_items[:8]:
                bp = it.get("last_price") or DEFAULT_PRICE_PLACEHOLDER
                dp_str = apply_promo_to_price(bp, promo_obj)
                title_esc = esc(it.get("title") or "Товар")
                url_esc = html.escape(it.get("url") or "")
                if dp_str:
                    lines.append(f"• <a href=\"{url_esc}\">{title_esc}</a>: <code>{esc(bp)}</code> ➡️ <b>{esc(dp_str)}</b>")

        row1 = []
        if promo_code and brand != "Магазин":
            row1.append(InlineKeyboardButton(text="✅ Применить к списку", callback_data=f"emapply_{key}"))
        if CHANNEL_ID:
            row1.append(InlineKeyboardButton(text="📢 В канал", callback_data=f"empub_{key}"))
        buttons = []
        if row1:
            buttons.append(row1)
        if em.get("link"):
            buttons.append([InlineKeyboardButton(text="🛒 Открыть магазин", url=em["link"])])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

        try:
            await bot.send_message(owner_id, "\n".join(lines), reply_markup=kb, disable_web_page_preview=True)
            sent_count += 1
        except Exception as e:
            logger.warning("⚠️ Не удалось отправить письмо-промо в ЛС: %s", e)

    return sent_count


@dp.callback_query(F.data.startswith("emapply_"))
async def cb_apply_email_promo(callback: types.CallbackQuery):
    key = callback.data.split("_", 1)[1]
    em = PENDING_EMAIL_PROMOS.get(key)
    if not em or not em.get("promo_code"):
        await callback.answer("⚠️ Промокод устарел.", show_alert=True)
        return
    brand = em["brand"]
    register_store_promo(
        brand,
        em["promo_code"],
        pct=em.get("discount_pct"),
        fixed=em.get("fixed_disc"),
        fixed_cur=em.get("fixed_cur"),
        source="почта",
    )
    await callback.answer(f"✅ Код {em['promo_code']} применён для {brand}!", show_alert=True)


@dp.callback_query(F.data.startswith("empub_"))
async def cb_publish_email_promo(callback: types.CallbackQuery):
    key = callback.data.split("_", 1)[1]
    em = PENDING_EMAIL_PROMOS.get(key)
    if not em:
        await callback.answer("⚠️ Данные письма устарели.", show_alert=True)
        return
    if not CHANNEL_ID:
        await callback.answer("⚠️ CHANNEL_ID не задан.", show_alert=True)
        return
    brand = em["brand"]
    lines = [
        f"<b>PROMO SIGNAL | {brand_to_hashtag(brand)}</b>",
        "",
        f"<b>{esc(em['subject'])}</b>",
    ]
    if em.get("promo_code"):
        disc = f" (-{em['discount_pct']}%)" if em.get("discount_pct") else ""
        lines.append(f"🎟 <b>Промокод:</b> <code>{esc(em['promo_code'])}</code>{disc}")
    elif em.get("discount_pct"):
        lines.append(f"🔥 <b>Скидка:</b> <b>-{em['discount_pct']}%</b>")
    if em.get("valid_until"):
        lines.append(f"⏳ <b>Действует:</b> <code>{esc(em['valid_until'])}</code>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Перейти в магазин", url=em["link"])]]) if em.get("link") else None
    try:
        await bot.send_message(CHANNEL_ID, "\n".join(lines), reply_markup=kb, disable_web_page_preview=True)
        await callback.answer("📢 Опубликовано в канал!", show_alert=True)
    except Exception as e:
        logger.error("Publish email promo error: %s", e)
        await callback.answer("⚠️ Ошибка публикации.", show_alert=True)


@dp.message(Command("checkmail"))
async def cmd_checkmail(message: types.Message, state: FSMContext):
    await state.clear()
    if not EMAIL_USER or not EMAIL_PASS:
        await message.answer("⚠️ Почта не настроена (нет <code>EMAIL_USER</code> / <code>EMAIL_PASS</code> в <code>.env</code>).")
        return
    status = await message.answer("⏳ Проверяю непрочитанные письма на почте...")
    try:
        count = await process_email_promos(target_user_id=message.from_user.id)
    except Exception as e:
        logger.error("Manual checkmail error: %s", e)
        await status.edit_text(f"⚠️ Ошибка подключения к почте: <code>{esc(str(e))}</code>")
        return
    try:
        await status.delete()
    except Exception:
        pass
    if count == 0:
        await message.answer("📭 Новых непрочитанных писем от магазинов пока нет.", reply_markup=get_main_keyboard())
    else:
        await message.answer(f"✅ Обработано новых писем: <b>{count}</b>", reply_markup=get_main_keyboard())


async def run_email_watcher():
    while True:
        try:
            count = await process_email_promos()
            if count:
                logger.info("📩 Обработано %d писем с почты %s", count, EMAIL_USER)
        except Exception as e:
            logger.warning("⚠️ Ошибка проверки почты (%s): %s", IMAP_HOST, e)
        await asyncio.sleep(EMAIL_CHECK_INTERVAL)


def _source_name_from_deal_id(deal_id: str | None) -> str:
    if not deal_id:
        return "Другие"
    low = deal_id.lower()
    mapping = [
        ("slickdeals", "Slickdeals"),
        ("dealnews", "DealNews"),
        ("sneakersteal", "SneakerSteal"),
        ("mydealz", "MyDealz (DE)"),
        ("hotukdeals", "HotUKDeals (UK)"),
        ("chollometro", "Chollometro (ES)"),
        ("dealabs", "Dealabs (FR)"),
        ("pepper.pl", "Pepper (PL)"),
        ("pepper.it", "Pepper (IT)"),
        ("reddit", "Reddit"),
    ]
    for token, label in mapping:
        if token in low:
            return label
    return urlparse(deal_id).netloc or "Другие"


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message, state: FSMContext):
    await state.clear()
    now_utc = datetime.now(timezone.utc)
    since_24h = (now_utc - timedelta(hours=24)).isoformat()
    since_7d = (now_utc - timedelta(days=7)).isoformat()

    try:
        res_7d = await db_exec(
            lambda: supabase.table("posted_deals")
            .select("brand,deal_id,created_at")
            .gte("created_at", since_7d)
            .limit(1000)
            .execute()
        )
        res_tracked = await db_exec(
            lambda: supabase.table("tracked_items").select("id").eq("user_id", message.from_user.id).execute()
        )
    except Exception as e:
        logger.error("Stats DB error: %s", e)
        await message.answer("⚠️ Ошибка загрузки статистики.", reply_markup=get_main_keyboard())
        return

    rows_7d = res_7d.data or []
    rows_24h = [r for r in rows_7d if (r.get("created_at") or "") >= since_24h]
    brand_counts = Counter((r.get("brand") or "Другие") for r in rows_7d).most_common(5)
    source_counts = Counter(_source_name_from_deal_id(r.get("deal_id")) for r in rows_7d).most_common(5)

    uptime_sec = int(time.time() - BOT_START_TS)
    up_h, up_m = divmod(uptime_sec // 60, 60)
    now_ts = time.time()
    active_promos_cnt = sum(1 for p in ACTIVE_STORE_PROMOS.values() if (now_ts - p["ts"]) < PROMO_TTL_SECONDS)
    quiet_status = "🌙 Тихий режим (ночь)" if is_quiet_hours() else "🔔 Обычный режим"
    mail_status = f"✅ {EMAIL_USER}" if (EMAIL_USER and EMAIL_PASS) else "выкл"

    lines = [
        "📊 <b>Статистика USA Drop Radar</b>\n",
        f"• Дропов за 24 часа: <b>{len(rows_24h)}</b>",
        f"• Дропов за 7 дней: <b>{len(rows_7d)}</b>",
        f"• Твоих товаров в трекере: <b>{len(res_tracked.data or [])}</b>",
        f"• Активных промокодов сегодня: <b>{active_promos_cnt}</b>",
        f"• RSS-источников: <b>{len(STANDARD_RSS_SOURCES) + len(REDDIT_RSS_SOURCES)}</b>",
        f"• Почта: <code>{esc(mail_status)}</code>",
        f"• Канал сейчас: {quiet_status}",
        f"• Аптайм бота: <b>{up_h}ч {up_m}м</b>",
    ]
    if brand_counts:
        lines.append("\n🏷 <b>Топ брендов (7 дней):</b>")
        for b, c in brand_counts:
            lines.append(f"  • {esc(b)} — <b>{c}</b>")
    if source_counts:
        lines.append("\n🌐 <b>Топ площадок (7 дней):</b>")
        for s_name, c in source_counts:
            lines.append(f"  • {esc(s_name)} — <b>{c}</b>")

    await message.answer("\n".join(lines), reply_markup=get_main_keyboard())


async def build_daily_digest_text() -> str | None:
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        res = await db_exec(
            lambda: supabase.table("posted_deals")
            .select("*")
            .gte("created_at", since_24h)
            .order("id", desc=True)
            .limit(120)
            .execute()
        )
    except Exception as e:
        logger.warning("Digest DB error: %s", e)
        return None

    rows = res.data or []
    if not rows:
        return None

    def _score(row: dict) -> tuple[int, float]:
        pct = row.get("discount_pct")
        if not pct:
            pct = extract_discount_pct(row.get("title") or "", "") or 0
        usd = price_to_usd(row.get("price"))
        price_bonus = -(usd if usd is not None else 999.0)
        return (int(pct), price_bonus)

    ranked = sorted(rows, key=_score, reverse=True)
    picked = []
    seen_brands = Counter()
    for row in ranked:
        b = (row.get("brand") or "").lower()
        if seen_brands[b] >= 2:
            continue
        seen_brands[b] += 1
        picked.append(row)
        if len(picked) >= 5:
            break

    if not picked:
        return None

    today_str = datetime.now(KYIV_TZ).strftime("%d.%m.%Y")
    lines = [f"🔥 <b>ТОП-{len(picked)} ДРОПОВ ДНЯ ({today_str})</b>\n"]
    for idx, row in enumerate(picked, 1):
        brand = esc(row.get("brand") or "Drop")
        title = esc(row.get("title") or "Скидка")
        price_str = row.get("price") or ""
        pct = row.get("discount_pct") or extract_discount_pct(row.get("title") or "", "")
        pct_tag = f" <b>(-{pct}%)</b>" if pct else ""
        price_tag = f" — <code>{esc(format_price_multi(price_str))}</code>" if price_str else ""
        url = row.get("url")
        if url:
            lines.append(f"{idx}. <b>[{brand}]</b> <a href=\"{html.escape(url)}\">{title}</a>{pct_tag}{price_tag}")
        else:
            lines.append(f"{idx}. <b>[{brand}]</b> {title}{pct_tag}{price_tag}")

    lines.append("\n<i>Все актуальные скидки — выше в ленте ⬆️</i>")
    return "\n".join(lines)


@dp.message(Command("digest"))
async def cmd_digest(message: types.Message, state: FSMContext):
    await state.clear()
    text = await build_daily_digest_text()
    if not text:
        await message.answer("📭 За последние 24 часа пока нет дропов для дайджеста.", reply_markup=get_main_keyboard())
        return
    parts = (message.text or "").split()
    if len(parts) >= 2 and parts[1].lower() == "post" and CHANNEL_ID:
        await bot.send_message(CHANNEL_ID, text, disable_web_page_preview=True, disable_notification=is_quiet_hours())
        await message.answer("📢 Дайджест опубликован в канал!", reply_markup=get_main_keyboard())
    else:
        await message.answer(text + "\n\n<i>Чтобы отправить этот дайджест в канал сейчас, напиши:</i> <code>/digest post</code>", disable_web_page_preview=True, reply_markup=get_main_keyboard())


async def run_daily_digest_loop():
    global LAST_DIGEST_DATE
    while True:
        try:
            now_kyiv = datetime.now(KYIV_TZ)
            today_key = now_kyiv.strftime("%Y-%m-%d")
            if now_kyiv.hour == DIGEST_HOUR and now_kyiv.minute < 15 and LAST_DIGEST_DATE != today_key:
                text = await build_daily_digest_text()
                if text and CHANNEL_ID:
                    await bot.send_message(CHANNEL_ID, text, disable_web_page_preview=True, disable_notification=is_quiet_hours())
                    LAST_DIGEST_DATE = today_key
                    logger.info("📰 Вечерний дайджест дня опубликован в канал!")
        except Exception as e:
            logger.warning("⚠️ Ошибка вечернего дайджеста: %s", e)
        await asyncio.sleep(300)


async def update_rates_loop():
    while True:
        await asyncio.sleep(14400)
        await load_exchange_rates()
        await fetch_nike_active_promo()


async def handle_ping(request: web.Request) -> web.Response:
    return web.Response(text="Live!")


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("🌐 Healthcheck-сервер запущен на порту %d", port)
    return runner


async def main():
    global http_session, BOT_USERNAME
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        logger.info("🤖 Бот авторизован: @%s", BOT_USERNAME)
    except Exception as e:
        logger.warning("⚠️ Не удалось получить username бота: %s", e)

    await load_exchange_rates()
    await load_active_promos_from_db()
    await fetch_nike_active_promo()
    web_runner = await start_web_server()

    tasks = [
        asyncio.create_task(check_personal_tracker()),
        asyncio.create_task(update_rates_loop()),
    ]
    if CHANNEL_ID:
        tasks.append(asyncio.create_task(run_radar_standard()))
        tasks.append(asyncio.create_task(run_radar_reddit()))
        tasks.append(asyncio.create_task(run_daily_digest_loop()))
    else:
        logger.warning("⚠️ CHANNEL_ID не задан — публикация RSS-дропов в канал отключена.")

    if EMAIL_USER and EMAIL_PASS:
        tasks.append(asyncio.create_task(run_email_watcher()))
        logger.info("📩 Мониторинг почты включён (%s @ %s)", EMAIL_USER, IMAP_HOST)

    logger.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await web_runner.cleanup()
        await bot.session.close()
        if http_session:
            await http_session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
