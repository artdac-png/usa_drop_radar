import asyncio
import os
import re
import feedparser
import aiohttp
from urllib.parse import urlparse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from supabase import create_client, Client
from aiohttp import web

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- 1. Только целевые бренды одежды и обуви ---
TARGET_BRANDS = [
    "jordan", "nike", "adidas", "puma", "new balance", "under armour", "converse",
    "lacoste", "tommy hilfiger", "tommy", "calvin klein", "calvin", "guess", 
    "michael kors", "kors", "coach", "levi's", "levis", "levi", "boss", "hugo boss",
    "zara", "mango", "massimo dutti", "massimo", "h&m", "h m", "hm",
    "asics", "salomon", "solomon", "saucony", "soucone", "mizuno", "hoka",
    "on running", "onrunning", "on-running",
    "the north face", "north face", "tnf"
]

# --- 2. Исключения (Часы, Электроника, Парфюмерия) ---
EXCLUDE_STOP_WORDS = [
    "watch", "watches", "chronograph", "timepiece", "smartwatch", "quartz", "dial", "clock",
    "headphone", "headphones", "earbuds", "earphone", "headset", "speaker", 
    "laptop", "tv", "television", "monitor", "phone", "smartphone", "audio", 
    "amplifier", "soundbar", "drive", "ssd", "gpu", "cpu",
    "perfume", "fragrance", "cologne", "eau de", "parfum", "toilette"
]

CASHBACK_MAP = {
    "Jordan": "TopCashback / Rakuten / Beruby (до 8-10%)",
    "Nike": "TopCashback / Beruby / Rakuten (до 8-10%)",
    "Adidas": "TopCashback / Beruby (до 6-8%)",
    "Puma": "TopCashback / iGraal (до 10%)",
    "New balance": "TopCashback / Rakuten (до 5%)",
    "The north face": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "North face": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Tnf": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Converse": "TopCashback / Rakuten (до 6-8%)",
    "Lacoste": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Tommy hilfiger": "TopCashback / Rakuten (до 7%)",
    "Tommy": "TopCashback / Rakuten (до 7%)",
    "Calvin klein": "TopCashback / Rakuten (до 7%)",
    "Calvin": "TopCashback / Rakuten (до 7%)",
    "Guess": "TopCashback / iGraal (до 6%)",
    "Michael kors": "TopCashback / Rakuten (до 6%)",
    "Kors": "TopCashback / Rakuten (до 6%)",
    "Coach": "TopCashback / Rakuten (до 6%)",
    "Levi's": "TopCashback / iGraal (до 8%)",
    "Levis": "TopCashback / iGraal (до 8%)",
    "Levi": "TopCashback / iGraal (до 8%)",
    "Boss": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Hugo boss": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Zara": "TopCashback / Beruby / iGraal (до 4-5%)",
    "Mango": "TopCashback / iGraal / Beruby (до 6-8%)",
    "Massimo dutti": "TopCashback / Beruby / iGraal (до 5-7%)",
    "Massimo": "TopCashback / Beruby / iGraal (до 5-7%)",
    "H&m": "TopCashback / Rakuten / iGraal (до 4-6%)",
    "Asics": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Salomon": "TopCashback / Rakuten / iGraal (до 5-7%)",
    "Solomon": "TopCashback / Rakuten / iGraal (до 5-7%)",
    "Saucony": "TopCashback / Rakuten (до 6-8%)",
    "Soucone": "TopCashback / Rakuten (до 6-8%)",
    "Mizuno": "TopCashback / Rakuten (до 5-7%)",
    "Hoka": "TopCashback / Rakuten / iGraal (до 5-8%)",
    "On running": "TopCashback / Rakuten / iGraal (до 5-8%)",
    "Onrunning": "TopCashback / Rakuten / iGraal (до 5-8%)",
    "On-running": "TopCashback / Rakuten / iGraal (до 5-8%)"
}

RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+jordan+adidas+lacoste+tommy+calvin+asics+salomon+saucony+mizuno+hoka+onrunning+levis+northface&rss=1"},
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
    {"flag": "🇵🇱 [Польша / PL]", "url": "https://www.pepper.pl/rss/grupa/moda-i-akcesoria"}
]

def find_matched_brand(title: str) -> str:
    title_lower = title.lower()
    if any(stop in title_lower for stop in EXCLUDE_STOP_WORDS):
        return None

    for brand in TARGET_BRANDS:
        pattern = r'\b' + re.escape(brand) + r'\b'
        if re.search(pattern, title_lower):
            if brand in ["h&m", "h m", "hm"]:
                return "H&M"
            if brand in ["massimo dutti", "massimo"]:
                return "Massimo Dutti"
            if brand in ["salomon", "solomon"]:
                return "Salomon"
            if brand in ["saucony", "soucone"]:
                return "Saucony"
            if brand in ["on running", "onrunning", "on-running"]:
                return "On Running"
            if brand in ["the north face", "north face", "tnf"]:
                return "The North Face"
            if brand in ["levi's", "levis", "levi"]:
                return "Levi's"
            if brand in ["boss", "hugo boss"]:
                return "Hugo Boss"
            if brand in ["calvin klein", "calvin"]:
                return "Calvin Klein"
            return brand.capitalize()
    return None

def extract_price(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    spend_match = re.findall(r'(\d+%\s*(?:off|від|от)\s*(?:[\$\b\d\.\,€£\s]+|\$\d+\+?))', text, re.IGNORECASE)
    if spend_match:
        return f"🔥 СЕЙЛ ({spend_match[0].upper()})"

    bundle_match = re.findall(r'(\b(?:buy\s*)?\d+\s*(?:for|por)\s*[\$\b\d\.\,€£\s]+(?:\$\d+|\d+€|\d+£)?)', text, re.IGNORECASE)
    if bundle_match:
        return bundle_match[0].strip()

    price_pattern = r'(\$\d+(?:\.\d{2})?|\b\d+(?:[.,]\d{1,2})?\s*€|€\s*\d+(?:[.,]\d{1,2})?|£\d+(?:\.\d{2})?)'
    match_title = re.findall(price_pattern, title)
    if match_title:
        return match_title[0]
        
    if summary:
        match_summary = re.findall(price_pattern, summary)
        if match_summary:
            return match_summary[0]

    pct_match = re.findall(r'(\d+%\s*off|extra\s*\d+%)', text, re.IGNORECASE)
    if pct_match:
        return f"🔥 СЕЙЛ ({pct_match[0].upper()})"
            
    return "🔥 СЕЙЛ НА САЙТЕ"

def extract_best_link(summary: str, default_link: str) -> str:
    if not summary:
        return default_link
    urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', summary, re.IGNORECASE)
    redirect_kws = ["sdtid", "buyfromdeal", "pno=", "lw/", "redirect", "out/", "checkout", "couponcabin", "bradsdeals"]
    for url in urls:
        if any(kw in url.lower() for kw in redirect_kws):
            return url
    forum_domains = ["slickdeals.net", "mydealz.de", "chollometro.com", "hotukdeals.com", "dealabs.com", "pepper.pl", "pepper.it", "dealnews.com", "bradsdeals.com", "couponcabin.com"]
    for url in urls:
        if not any(domain in url.lower() for domain in forum_domains):
            return url
    return default_link

def extract_hd_image(entry, summary: str) -> str:
    url = None
    if hasattr(entry, 'media_content') and entry.media_content:
        url = entry.media_content[0]['url']
    elif hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        url = entry.media_thumbnail[0]['url']
    elif hasattr(entry, 'enclosures') and entry.enclosures:
        url = entry.enclosures[0].get('href')

    raw_html = ""
    if hasattr(entry, 'content') and entry.content:
        raw_html += entry.content[0].value
    if summary:
        raw_html += " " + summary

    if not url and raw_html:
        img_matches = re.findall(r'(?:src|data-src)=["\']([^"\']+)["\']', raw_html, re.IGNORECASE)
        if img_matches:
            for img_url in img_matches:
                if not any(stop in img_url.lower() for stop in ["beacon", "tracking", ".gif", "1x1", "avatar"]):
                    url = img_url
                    break
    if not url:
        return None

    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('/'):
        url = 'https://slickdeals.net' + url

    if "slickdeals.net" in url:
        url = re.sub(r'/\d+x\d+/', '/800x800/', url)
        url = re.sub(r'_(?:thumb|small|medium)\
