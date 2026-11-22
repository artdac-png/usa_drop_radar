import asyncio
import html
import logging
import os
import re
from collections import deque
from urllib.parse import parse_qs, urljoin, urlparse

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

RADAR_STANDARD_INTERVAL = int(os.getenv("RADAR_STANDARD_INTERVAL", "600"))
RADAR_REDDIT_INTERVAL = int(os.getenv("RADAR_REDDIT_INTERVAL", "900"))
TRACKER_INTERVAL = int(os.getenv("TRACKER_INTERVAL", "10800"))

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("❌ Ошибка: Не заданы переменные окружения (BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY)!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
http_session: aiohttp.ClientSession | None = None
EXCHANGE_RATES: dict[str, float] = {}

DEFAULT_PRICE_PLACEHOLDER = "🔥 СМОТРЕТЬ НА САЙТЕ"
RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; USADropRadarBot/2.0; +https://github.com/artdac-png/usa_drop_radar)",
    "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml;q=0.9, */*;q=0.8",
}

# In-memory recent cache to avoid repeated DB queries on every RSS cycle
RECENT_DEAL_IDS: deque[str] = deque(maxlen=1000)
RECENT_DEAL_IDS_SET: set[str] = set()
RECENT_TITLES: deque[str] = deque(maxlen=1000)
RECENT_TITLES_SET: set[str] = set()


class TrackState(StatesGroup):
    waiting_for_url = State()


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
    ("tommy", "Tommy Hilfiger"),
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
    ("boss", "Hugo Boss"),
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
    ("champs sports", "Champs Sports"),
    ("champs", "Champs Sports"),
    ("nordstrom rack", "Nordstrom Rack"),
    ("nordstrom", "Nordstrom Rack"),
    ("rack room shoes", "Rack Room Shoes"),
    ("rack room", "Rack Room Shoes"),
    ("footlocker", "Footlocker"),
    ("foot locker", "Footlocker"),
    ("jd sports", "JD Sports"),
    ("zappos", "Zappos"),
    ("asos", "ASOS"),
    ("mandm", "MandM"),
    ("shop simon", "Shop Simon"),
    ("joes new balance", "Joes New Balance"),
    ("woot", "Woot"),
    ("shoebacca", "Shoebacca"),
    ("macy's", "Macy's"),
    ("macys", "Macy's"),
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
    "keyboard", "wagon", "gaming", "computer", "mouse", "toy", "toys", "tools", "kitchen", "vacuum", "printer",
    "dehumidifier", "frigidaire", "air filter", "appliance", "heater", "cooler", "fridge", "refrigerator", "washing", "dryer", "microwave", "oven", "blender",
]

# Word-boundary regex prevents false positives like "pullover" (over), "bags" (gs), "tops" (ps), "in cart" (cart), "boyfriend" (boy)
STOP_WORDS_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in EXCLUDE_STOP_WORDS) + r")\b",
    re.IGNORECASE,
)

STANDARD_RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+jordan+adidas+lacoste+tommy+calvin+asics+salomon+saucony+mizuno+hoka+onrunning+levis+northface+shoebacca+macys+gap+oldnavy&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.dealnews.com/c196/Clothing-Accessories/?rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.bradsdeals.com/rss/fashion"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.bradsdeals.com/rss/coupons"},
    {"flag": "🇺🇸 [США / US]", "url": "https://rss.couponcabin.com/coupons/clothing-and-apparel.xml"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.hotukdeals.com/rss/tag/fashion"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.hotukdeals.com/rss/tag/sports-fitness"},
    {"flag": "🇩🇪 [Германия / DE]", "url": "https://www.mydealz.de/rss/gruppe/fashion"},
    {"flag": "🇩🇪 [Германия / DE]", "url": "https://www.mydealz.de/rss/gruppe/sport"},
    {"flag": "🇪🇸 [Испания / ES]", "url": "https://www.chollometro.com/rss/grupo/moda-y-accesorios"},
    {"flag": "🇪🇸 [Испания / ES]", "url": "https://www.chollometro.com/rss/grupo/deportes"},
    {"flag": "🇫🇷 [Франция / FR]", "url": "https://www.dealabs.com/rss/groupe/mode-et-accessoires"},
    {"flag": "🇮🇹 [Италия / IT]", "url": "https://www.pepper.it/rss/gruppo/moda-e-accessori"},
    {"flag": "🇵🇱 [Польша / PL]", "url": "https://www.pepper.pl/rss/grupa/moda-i-akcesoria"},
]

REDDIT_RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/frugalmalefashion/.rss"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/SneakerDeals/.rss"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.reddit.com/r/FrugalMaleFashionUK/.rss"},
]


async def db_exec(fn):
    """Run synchronous Supabase SDK calls in a worker thread to avoid blocking the event loop."""
    return await asyncio.to_thread(fn)


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
        async with http_session.get("https://open.er-api.com/v6/latest/USD", timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
    """Return (numeric_amount, currency_code) from a price string like '$49.99' or '39,90 €'."""
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
    """Build direct Cloudinary hero image URL for The North Face product pages."""
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


def extract_page_image(page_html: str, page_url: str) -> str | None:
    """Extract product image from og:image, twitter:image, JSON-LD schema.org image, or link rel=image_src."""
    for prop in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        val = extract_meta_content(page_html, prop)
        norm = normalize_image_url(val, page_url)
        if norm:
            return norm

    # JSON-LD / Schema.org "image": "https://..." or "image": ["https://...", ...]
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


def extract_meta_content(page_html: str, prop_name: str) -> str | None:
    """Extract meta tag content regardless of attribute order or quote style, preserving original case."""
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


def is_out_of_stock(page_html: str) -> bool:
    """Check structured availability signals to avoid false positives from JS/CSS strings."""
    if re.search(r'"availability"\s*:\s*"(?:https?://schema\.org/)?OutOfStock"', page_html, re.IGNORECASE):
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


async def fetch_product_data(url: str) -> tuple[str, str | None, str | None, str | None]:
    price = DEFAULT_PRICE_PLACEHOLDER
    url_lower = url.lower()
    image_url: str | None = None
    if "levi.com" in url_lower:
        image_url = extract_levis_image(url)
    elif "thenorthface.com" in url_lower:
        image_url = extract_tnf_image(url)
    parsed_title: str | None = None
    parsed_brand: str | None = None

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
    except Exception as e:
        logger.warning("⚠️ Ошибка curl_cffi для %s: %s", url, e)

    if (price == DEFAULT_PRICE_PLACEHOLDER or not image_url) and GAS_PROXY_URL and http_session:
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
        except Exception as e:
            logger.debug("GAS proxy fallback error for %s: %s", url, e)

    if parsed_title and any(w in parsed_title.lower() for w in ["oops", "error", "access denied", "not found", "forbidden", "just a moment", "attention required"]):
        parsed_title = None
        if not ("levi.com" in url_lower or "thenorthface.com" in url_lower):
            image_url = None

    return price, image_url, parsed_title, parsed_brand


async def send_photo_or_text(
    chat_id: int | str,
    text: str,
    image_url: str | None = None,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
    disable_web_page_preview: bool = True,
) -> bool:
    """Send photo with caption (auto-truncated to 1024 chars), falling back to plain text on photo error."""
    if image_url:
        caption = text if len(text) <= 1024 else text[:1020] + "..."
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=caption,
                reply_markup=reply_markup,
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
    )
    return True


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить ссылку"), KeyboardButton(text="📋 Мой список")],
            [KeyboardButton(text="💱 Курсы валют"), KeyboardButton(text="ℹ️ Помощь")],
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


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 <b>Привет! Я USA Drop Radar.</b>\n\n"
        "• Автоматически мониторю скидки на брендовую одежду и обувь.\n"
        "• Могу отслеживать цены на любые твои ссылки и сообщать об изменениях.\n\n"
        "Выбери действие в меню ниже 👇",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📖 <b>Команды бота:</b>\n\n"
        "• /track или <b>➕ Добавить ссылку</b> — добавить товар для отслеживания цены\n"
        "• /list или <b>📋 Мой список</b> — посмотреть и проверить свои товары\n"
        "• /rates или <b>💱 Курсы валют</b> — актуальные курсы USD / EUR / GBP / UAH\n"
        "• /cancel — отменить текущее действие",
        reply_markup=get_main_keyboard(),
    )


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
    await state.set_state(TrackState.waiting_for_url)
    await message.answer(
        "🔗 <b>Отправь мне ссылку на вещь</b> (можно просто вставить сообщение со ссылкой):",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(
    TrackState.waiting_for_url,
    ~F.text.startswith("/"),
    ~F.text.in_({"📋 Мой список", "➕ Добавить ссылку", "💱 Курсы валют", "ℹ️ Помощь", "❌ Отмена"}),
)
async def process_track_url(message: types.Message, state: FSMContext):
    url = extract_url_from_text(message.text)
    if not url:
        await message.answer("⚠️ Не нашёл ссылку в сообщении. Отправь URL вида <code>https://...</code> или нажми <b>❌ Отмена</b>.")
        return

    # Check for duplicate URL for this user
    try:
        existing = await db_exec(
            lambda: supabase.table("tracked_items")
            .select("id")
            .eq("user_id", message.from_user.id)
            .eq("url", url)
            .execute()
        )
        if existing.data:
            await state.clear()
            await message.answer("ℹ️ Эта ссылка уже есть в твоём списке отслеживания.", reply_markup=get_main_keyboard())
            return
    except Exception as e:
        logger.error("DB duplicate check error: %s", e)

    status_msg = await message.answer("⏳ Сканирую страницу товара...")
    price, image_url, parsed_title, parsed_brand = await fetch_product_data(url)
    title = clean_text(parsed_title or extract_title_from_url(url))
    brand = clean_text(parsed_brand or find_matched_brand(title) or find_matched_brand(url) or "Брендовая вещь")

    try:
        res = await db_exec(
            lambda: supabase.table("tracked_items")
            .insert({
                "user_id": message.from_user.id,
                "url": url,
                "brand": brand,
                "title": title,
                "last_price": price,
                "image_url": image_url,
            })
            .execute()
        )
        if not res.data:
            await message.answer("⚠️ Ошибка сохранения в базу.", reply_markup=get_main_keyboard())
            await state.clear()
            return
        item_id = res.data[0]["id"]
    except Exception as e:
        logger.error("DB insert error: %s", e)
        await message.answer("⚠️ Ошибка БД при сохранении.", reply_markup=get_main_keyboard())
        await state.clear()
        return

    await state.clear()
    try:
        await status_msg.delete()
    except Exception:
        pass

    price_display = format_price_multi(price)
    caption = (
        "✅ <b>Сохранено!</b>\n\n"
        f"🏷 <b>Бренд:</b> {esc(brand)}\n"
        f"📦 <b>Модель:</b> {esc(title)}\n"
        f"💳 <b>Цена:</b> <code>{esc(price_display)}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛒 Открыть", url=url),
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{item_id}"),
            ]
        ]
    )
    await send_photo_or_text(message.chat.id, caption, image_url=image_url, reply_markup=kb)
    await message.answer("Товар добавлен в отслеживание 👇", reply_markup=get_main_keyboard())


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
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🛒 Перейти", url=item["url"]),
                    InlineKeyboardButton(text="🔄 Проверить", callback_data=f"chk_{item['id']}"),
                    InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{item['id']}"),
                ]
            ]
        )
        price_display = format_price_multi(item.get("last_price") or DEFAULT_PRICE_PLACEHOLDER)
        caption = (
            f"🏷 <b>Бренд:</b> {esc(item.get('brand') or 'Одежда')}\n"
            f"📦 <b>Модель:</b> {esc(item.get('title') or 'Товар')}\n"
            f"💳 <b>Цена:</b> <code>{esc(price_display)}</code>"
        )
        await send_photo_or_text(message.chat.id, caption, image_url=item.get("image_url"), reply_markup=kb)


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


@dp.callback_query(F.data.startswith("chk_"))
async def manual_check_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_", 1)[1]
    await callback.answer("⏳ Проверяю цену...")
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

    item = res.data[0]
    new_price, new_image, _, _ = await fetch_product_data(item["url"])

    # Backfill missing photo in DB if we found one now
    image_to_use = item.get("image_url")
    if not image_to_use and new_image:
        image_to_use = new_image
        await db_exec(
            lambda: supabase.table("tracked_items").update({"image_url": new_image}).eq("id", item["id"]).execute()
        )

    if new_price == DEFAULT_PRICE_PLACEHOLDER:
        await callback.message.answer(f"⚠️ Не удалось получить актуальную цену для <b>{esc(item.get('title'))}</b>.")
        return

    old_amount, old_cur = parse_price_amount(item.get("last_price"))
    new_amount, new_cur = parse_price_amount(new_price)
    unchanged = (
        old_amount is not None
        and new_amount is not None
        and old_cur == new_cur
        and abs(old_amount - new_amount) < 0.01
    ) or new_price == item.get("last_price")

    if unchanged:
        await callback.message.answer(
            f"✅ Цена без изменений: <b>{esc(item.get('title'))}</b> — <code>{esc(format_price_multi(new_price))}</code>"
        )
        return

    await db_exec(
        lambda: supabase.table("tracked_items").update({"last_price": new_price}).eq("id", item["id"]).execute()
    )
    msg = build_price_change_message(item, new_price)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 КУПИТЬ", url=item["url"])]])
    await send_photo_or_text(callback.from_user.id, msg, image_url=image_to_use, reply_markup=kb)


def build_price_change_message(item: dict, new_price: str) -> str:
    old_price = item.get("last_price") or DEFAULT_PRICE_PLACEHOLDER
    old_amount, old_cur = parse_price_amount(old_price)
    new_amount, new_cur = parse_price_amount(new_price)

    header = "🔔 <b>ЦЕНА ИЗМЕНИЛАСЬ!</b>"
    diff_line = ""
    if old_amount and new_amount and old_cur == new_cur:
        pct = ((new_amount - old_amount) / old_amount) * 100
        if new_amount < old_amount:
            header = f"📉 <b>ЦЕНА СНИЗИЛАСЬ ({pct:.0f}%)!</b>"
        elif new_amount > old_amount:
            header = f"📈 <b>ЦЕНА ВЫРОСЛА (+{pct:.0f}%)</b>"
        diff_line = f"\n💸 <b>Стало:</b> <code>{esc(format_price_multi(new_price))}</code>"
    else:
        diff_line = f"\n💳 <b>Стало:</b> <code>{esc(format_price_multi(new_price))}</code>"

    return (
        f"{header}\n\n"
        f"🏷 <b>{esc(item.get('brand') or 'Бренд')}</b>\n"
        f"📦 {esc(item.get('title') or 'Товар')}\n"
        f"💳 <b>Было:</b> <code>{esc(old_price)}</code>"
        f"{diff_line}"
    )


async def check_personal_tracker():
    while True:
        try:
            res = await db_exec(lambda: supabase.table("tracked_items").select("*").execute())
            if res.data:
                logger.info("🔍 Проверка персонального трекера: %d товаров", len(res.data))
                for item in res.data:
                    new_price, new_image, _, _ = await fetch_product_data(item["url"])
                    image_to_use = item.get("image_url")
                    if not image_to_use and new_image:
                        image_to_use = new_image
                        try:
                            await db_exec(
                                lambda i=item, img=new_image: supabase.table("tracked_items")
                                .update({"image_url": img})
                                .eq("id", i["id"])
                                .execute()
                            )
                        except Exception:
                            pass
                    if new_price == DEFAULT_PRICE_PLACEHOLDER:
                        await asyncio.sleep(1)
                        continue

                    old_amount, old_cur = parse_price_amount(item.get("last_price"))
                    new_amount, new_cur = parse_price_amount(new_price)
                    is_same = (
                        old_amount is not None
                        and new_amount is not None
                        and old_cur == new_cur
                        and abs(old_amount - new_amount) < 0.01
                    ) or new_price == item.get("last_price")

                    if not is_same:
                        kb = InlineKeyboardMarkup(
                            inline_keyboard=[[InlineKeyboardButton(text="🛒 КУПИТЬ", url=item["url"])]]
                        )
                        msg = build_price_change_message(item, new_price)
                        try:
                            await send_photo_or_text(
                                item["user_id"], msg, image_url=image_to_use, reply_markup=kb
                            )
                            await db_exec(
                                lambda i=item, p=new_price: supabase.table("tracked_items")
                                .update({"last_price": p})
                                .eq("id", i["id"])
                                .execute()
                            )
                        except Exception as e:
                            logger.warning("⚠️ Не удалось уведомить пользователя %s: %s", item["user_id"], e)
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error("⚠️ Ошибка персонального трекера: %s", e)
        await asyncio.sleep(TRACKER_INTERVAL)


async def is_deal_already_posted(deal_id: str, title: str) -> bool:
    if deal_id in RECENT_DEAL_IDS_SET or title in RECENT_TITLES_SET:
        return True
    try:
        res_id = await db_exec(
            lambda: supabase.table("posted_deals").select("id").eq("deal_id", deal_id).limit(1).execute()
        )
        if res_id.data:
            remember_deal(deal_id, title)
            return True
        res_title = await db_exec(
            lambda: supabase.table("posted_deals").select("id").eq("title", title).limit(1).execute()
        )
        if res_title.data:
            remember_deal(deal_id, title)
            return True
    except Exception as e:
        logger.warning("⚠️ Ошибка проверки дубликата в БД: %s", e)
    return False


async def process_entry(entry, flag: str):
    title = clean_text(getattr(entry, "title", ""))
    main_link = getattr(entry, "link", "")
    summary = getattr(entry, "summary", "")
    if not title or not main_link:
        return

    brand = find_matched_brand(title)
    if not brand:
        return

    deal_id = getattr(entry, "id", main_link)
    if await is_deal_already_posted(deal_id, title):
        return

    price = extract_price(title, summary)
    target_link = extract_best_link(summary, main_link)
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

    price_with_multi = format_price_multi(price)
    buttons = [[InlineKeyboardButton(text="🛒 Купить (1 клик)", url=target_link)]]
    if main_link != target_link:
        buttons.append([InlineKeyboardButton(text="🔗 Источник", url=main_link)])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    post_text = (
        f"<b>DROP SIGNAL | {brand_to_hashtag(brand)}</b>\n"
        f"{esc(flag)}\n\n"
        f"<b>{esc(title)}</b>\n"
        f"💳 <b>Цена:</b> {esc(price_with_multi)}"
    )

    try:
        await send_photo_or_text(
            CHANNEL_ID,
            post_text,
            image_url=image_url,
            reply_markup=kb,
            disable_web_page_preview=False,
        )
        await db_exec(
            lambda: supabase.table("posted_deals")
            .insert({"deal_id": deal_id, "title": title, "brand": brand})
            .execute()
        )
        remember_deal(deal_id, title)
        logger.info("✅ Опубликован дроп: [%s] %s", brand, title[:80])
        await asyncio.sleep(3)
    except Exception as e:
        logger.error("⚠️ Ошибка публикации поста: %s", e)


async def fetch_feed(source: dict):
    url = source["url"]
    flag = source["flag"]
    if not http_session:
        return [], flag
    try:
        async with http_session.get(
            url,
            headers=RSS_HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.debug("RSS %s вернул статус %d", url, resp.status)
                return [], flag
            raw_xml = await resp.text()
        feed = await asyncio.to_thread(feedparser.parse, raw_xml)
        return feed.entries, flag
    except Exception as e:
        logger.debug("Ошибка загрузки RSS %s: %s", url, e)
        return [], flag


async def scan_and_post(sources: list[dict]):
    results = await asyncio.gather(*[fetch_feed(source) for source in sources])
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


async def update_rates_loop():
    while True:
        await asyncio.sleep(43200)
        await load_exchange_rates()


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
    global http_session
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    await load_exchange_rates()
    web_runner = await start_web_server()

    tasks = [
        asyncio.create_task(check_personal_tracker()),
        asyncio.create_task(update_rates_loop()),
    ]
    if CHANNEL_ID:
        tasks.append(asyncio.create_task(run_radar_standard()))
        tasks.append(asyncio.create_task(run_radar_reddit()))
    else:
        logger.warning("⚠️ CHANNEL_ID не задан — публикация RSS-дропов в канал отключена.")

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
