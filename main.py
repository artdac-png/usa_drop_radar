import asyncio
import os
import re
import html
import json
import feedparser
import aiohttp
from urllib.parse import urlparse, quote
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    ReplyKeyboardMarkup, 
    KeyboardButton
)
from dotenv import load_dotenv
from supabase import create_client, Client
from aiohttp import web
from curl_cffi.requests import AsyncSession

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GAS_PROXY_URL = os.getenv("GAS_PROXY_URL")

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("❌ Ошибка: Не заданы BOT_TOKEN, SUPABASE_URL или SUPABASE_KEY!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

http_session: aiohttp.ClientSession = None

class TrackState(StatesGroup):
    waiting_for_url = State()

class SearchState(StatesGroup):
    waiting_for_query = State()

# === СПИСОК ПЛОЩАДОК ДЛЯ ПОИСКА ===
SEARCH_SITES = [
    {"name": "Nike", "url": "https://www.nike.com/w?q={}", "domain": "nike.com"},
    {"name": "Adidas", "url": "https://www.adidas.com/us/search?q={}", "domain": "adidas.com"},
    {"name": "Footlocker US", "url": "https://www.footlocker.com/search?q={}", "domain": "footlocker.com"},
    {"name": "Footlocker UK", "url": "https://www.footlocker.co.uk/en/search?q={}", "domain": "footlocker.co.uk"},
    {"name": "JD Sports US", "url": "https://www.jdsports.com/search?q={}", "domain": "jdsports.com"},
    {"name": "JD Sports UK", "url": "https://www.jdsports.co.uk/search/{}", "domain": "jdsports.co.uk"},
    {"name": "Champs Sports", "url": "https://www.champssports.com/search?q={}", "domain": "champssports.com"},
    {"name": "Zappos", "url": "https://www.zappos.com/search?term={}", "domain": "zappos.com"},
    {"name": "Shop Simon", "url": "https://www.shopsimon.com/search?q={}", "domain": "shopsimon.com"},
    {"name": "ASOS US", "url": "https://www.asos.com/us/search/?q={}", "domain": "asos.com"},
    {"name": "Calvin Klein", "url": "https://www.calvinklein.us/en/search?q={}", "domain": "calvinklein.us"},
    {"name": "Levi's", "url": "https://www.levi.com/US/en_US/search?q={}", "domain": "levi.com"}
]

TARGET_BRANDS = [
    "jordan", "nike", "adidas", "puma", "new balance", "under armour", "converse",
    "lacoste", "tommy hilfiger", "tommy", "calvin klein", "calvin", "guess", 
    "michael kors", "kors", "coach", "levi's", "levis", "levi", "boss", "hugo boss",
    "zara", "mango", "massimo dutti", "massimo", "h&m", "h m", "hm",
    "asics", "salomon", "solomon", "saucony", "soucone", "mizuno", "hoka",
    "on running", "onrunning", "on-running", "the north face", "north face", "tnf"
]

EXCLUDE_STOP_WORDS = [
    "watch", "watches", "chronograph", "timepiece", "smartwatch", "quartz", "dial", "clock",
    "headphone", "headphones", "earbuds", "earphone", "headset", "speaker", 
    "laptop", "tv", "television", "monitor", "phone", "smartphone", "audio", 
    "amplifier", "soundbar", "drive", "ssd", "gpu", "cpu",
    "perfume", "fragrance", "cologne", "eau de", "parfum", "toilette",
    "giftset", "gift set", "body wash", "shower gel", "deodorant", "shampoo", "lotion", "cream", "soap", "skincare", "beauty",
    "kids", "toddler", "infant", "youth", "baby", "child", "children"
]

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

def clean_markdown_text(text: str) -> str:
    if not text: return ""
    text = html.unescape(text)
    text = re.sub(r'&reg;|®|&trade;|™', '', text, flags=re.IGNORECASE)
    return text.replace("_", " ").replace("*", "").replace("[", "").replace("]", "").replace("`", "").strip()

def find_matched_brand(text: str):
    if not text: return None
    text_lower = text.lower()
    if any(stop in text_lower for stop in EXCLUDE_STOP_WORDS): return None
    for brand in TARGET_BRANDS:
        pattern = r'\b' + re.escape(brand) + r'\b'
        if re.search(pattern, text_lower):
            if brand in ["h&m", "h m", "hm"]: return "H&M"
            if brand in ["massimo dutti", "massimo"]: return "Massimo Dutti"
            if brand in ["salomon", "solomon"]: return "Salomon"
            if brand in ["saucony", "soucone"]: return "Saucony"
            if brand in ["on running", "onrunning", "on-running"]: return "On Running"
            if brand in ["the north face", "north face", "tnf"]: return "The North Face"
            if brand in ["levi's", "levis", "levi"]: return "Levi's"
            if brand in ["boss", "hugo boss"]: return "Hugo Boss"
            if brand in ["calvin klein", "calvin"]: return "Calvin Klein"
            return brand.title()
    if "calvin" in text_lower or "calvinklein" in text_lower: return "Calvin Klein"
    if "levi.com" in text_lower or "levi" in text_lower: return "Levi's"
    return None

# === ФУНКЦИЯ ПОИСКА ПРОМОКОДОВ ===
def extract_promo_code(text: str):
    if not text: return None
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text).upper()
    
    patterns = [
        r'(?:PROMO|COUPON|DISCOUNT|VOUCHER)\s*CODE[:\s]+([A-Z0-9]{4,15})',
        r'USE\s*(?:PROMO\s*)?CODE[:\s]+([A-Z0-9]{4,15})',
        r'CODE[:\s]+([A-Z0-9]{4,15})',
        r'ПРОМОКОД[:\s]+([A-Z0-9]{4,15})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            code = match.group(1)
            if code not in ["SIZE", "SHIP", "ZIP", "FREE", "SAVE"]:
                return code
    return None

def extract_price(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    spend_match = re.findall(r'(\d+%\s*(?:off|від|от)\s*(?:[\$\d\.\,€£\s]+|\$\d+\+?))', text, re.IGNORECASE)
    if spend_match: return f"🔥 СЕЙЛ ({spend_match[0].upper()})"
    bundle_match = re.findall(r'(\b(?:buy\s*)?\d+\s*(?:for|por)\s*[\$\d\.\,€£\s]+(?:\$\d+|\d+€|\d+£)?)', text, re.IGNORECASE)
    if bundle_match: return bundle_match[0].strip()
    price_pattern = r'(\$\d+(?:\.\d{2})?|\b\d+(?:[.,]\d{1,2})?\s*€|€\s*\d+(?:[.,]\d{1,2})?|£\d+(?:\.\d{2})?)'
    match_title = re.findall(price_pattern, title)
    if match_title: return match_title[0]
    if summary:
        match_summary = re.findall(price_pattern, summary)
        if match_summary: return match_summary[0]
    pct_match = re.findall(r'(\d+%\s*off|extra\s*\d+%)', text, re.IGNORECASE)
    if pct_match: return f"🔥 СЕЙЛ ({pct_match[0].upper()})"
    return "🔥 СЕЙЛ НА САЙТЕ"

def extract_best_link(summary: str, default_link: str) -> str:
    if not summary: return default_link
    urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', summary, re.IGNORECASE)
    redirect_kws = ["sdtid", "buyfromdeal", "pno=", "lw/", "redirect", "out/", "checkout", "couponcabin", "bradsdeals"]
    for url in urls:
        if any(kw in url.lower() for kw in redirect_kws): return url
    forum_domains = ["slickdeals.net", "mydealz.de", "chollometro.com", "hotukdeals.com", "dealabs.com", "pepper.pl", "pepper.it", "dealnews.com", "bradsdeals.com", "couponcabin.com"]
    for url in urls:
        if not any(domain in url.lower() for domain in forum_domains): return url
    return default_link

def extract_hd_image(entry, summary: str) -> str:
    url = None
    if hasattr(entry, 'media_content') and entry.media_content: url = entry.media_content[0]['url']
    elif hasattr(entry, 'media_thumbnail') and entry.media_thumbnail: url = entry.media_thumbnail[0]['url']
    elif hasattr(entry, 'enclosures') and entry.enclosures: url = entry.enclosures[0].get('href')
    raw_html = ""
    if hasattr(entry, 'content') and entry.content: raw_html += entry.content[0].value
    if summary: raw_html += " " + summary
    if not url and raw_html:
        img_matches = re.findall(r'(?:src|data-src)=["\']([^"\']+)["\']', raw_html, re.IGNORECASE)
        if img_matches:
            for img_url in img_matches:
                if not any(stop in img_url.lower() for stop in ["beacon", "tracking", ".gif", "1x1", "avatar"]):
                    url = img_url; break
    if not url: return None
    if url.startswith('//'): url = 'https:' + url
    elif url.startswith('/'): url = 'https://slickdeals.net' + url
    if "slickdeals.net" in url:
        url = re.sub(r'/\d+x\d+/', '/800x800/', url); url = re.sub(r'_(?:thumb|small|medium)\.', '.', url)
    elif any(domain in url for domain in ["pepper.com", "static-pepper", "hotukdeals", "mydealz", "chollometro", "dealabs"]):
        url = re.sub(r'/re/\d+x\d+/qt/\d+/', '/', url)
    return url

def extract_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    ignore_words = ["en_us", "us", "en", "clothing", "men", "apparel", "mens-outerwear", "outerwear", "pants", "shoes", "p", "uk"]
    for part in reversed(path_parts):
        cleaned_part = re.sub(r'\.html$', '', part, flags=re.IGNORECASE)
        if re.search(r'\d', cleaned_part) and len(cleaned_part) < 20: continue
        if len(cleaned_part) > 3 and cleaned_part.lower() not in ignore_words and not cleaned_part.isdigit():
            return cleaned_part.replace("-", " ").title()
    return "Брендовая вещь"

def extract_levis_image(url: str) -> str:
    sku_match = re.search(r'/p/([A-Za-z0-9]+)', url)
    if sku_match: return f"https://lsco.scene7.com/is/image/lsco/{sku_match.group(1)}-front-pdp?wid=800&hei=800&fmt=jpeg&qlt=85"
    return None

async def fetch_product_data(url: str):
    price = "🔥 СМОТРЕТЬ НА САЙТЕ"
    image_url = extract_levis_image(url) if "levi.com" in url else None
    parsed_title = None
    parsed_brand = None
    try:
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome", timeout=15)
            html_text = resp.text
            if resp.status_code == 200:
                title_match = re.search(r'property="og:title"\s+content="([^"]+)"', html_text, re.IGNORECASE) or re.search(r'<title>([^<]+)</title>', html_text, re.IGNORECASE)
                if title_match: parsed_title = title_match.group(1).strip()
                img_match = re.search(r'property="og:image"\s+content="([^"]+)"', html_text, re.IGNORECASE)
                if img_match: image_url = img_match.group(1).strip()
                price_match = re.search(r'"price"\s*:\s*"?(\d+(?:\.\d{2})?)"?', html_text, re.IGNORECASE) or re.search(r'itemprop="price"\s+content="(\d+(?:\.\d{2})?)"', html_text, re.IGNORECASE)
                if price_match: price = f"${price_match.group(1)}"
    except Exception as e: print(f"⚠️ Ошибка curl_cffi: {e}")

    if price == "🔥 СМОТРЕТЬ НА САЙТЕ" and GAS_PROXY_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(GAS_PROXY_URL, params={"url": url}, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "success":
                            if data.get("price") and data.get("price") != "NOT_FOUND": price = data.get("price")
                            if data.get("image"): image_url = data.get("image")
                            if data.get("title") and "error" not in data.get("title").lower(): parsed_title = data.get("title")
                            if data.get("brand"): parsed_brand = data.get("brand")
        except Exception: pass

    if parsed_title:
        bad_words = ["oops", "error", "access denied", "not found", "forbidden", "something went wrong"]
        if any(word in parsed_title.lower() for word in bad_words):
            parsed_title = None; image_url = None
    return price, image_url, parsed_title, parsed_brand

# === УМНЫЙ ПОИСК СКИДОК (JSON-LD) ===
async def fetch_site_search(site, query):
    safe_query = f"{query} -kids -toddler -infant -youth -baby"
    encoded_query = quote(safe_query)
    url = site["url"].format(encoded_query)
    display_url = site["url"].format(quote(query))
    
    try:
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome", timeout=15)
            if resp.status_code == 200:
                html_text = resp.text
                json_scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.DOTALL)
                
                best_deal = None
                max_discount = 0
                
                for script in json_scripts:
                    try:
                        data = json.loads(script)
                        items = data if isinstance(data, list) else (data.get("@graph", [data]) if isinstance(data, dict) else [])
                        for item in items:
                            if item.get("@type") in ["Product", "ProductGroup"]:
                                offers = item.get("offers", [])
                                if isinstance(offers, dict): offers = [offers]
                                for offer in offers:
                                    price = float(offer.get("price", 0) or offer.get("lowPrice", 0))
                                    old_price = float(offer.get("highPrice", 0) or offer.get("listPrice", 0))
                                    if old_price > price and price > 40:
                                        discount = int(((old_price - price) / old_price) * 100)
                                        if discount > max_discount:
                                            max_discount = discount
                                            name = item.get("name", "Товар")
                                            product_url = item.get("url", display_url)
                                            best_deal = f"🔥 **СКИДКА {discount}%** | **{site['name']}**\n📦 {name[:40]}...\n💳 ~~${old_price:.0f}~~ ➡️ **${price:.0f}**\n🔗 [Купить]({product_url})"
                    except Exception: continue
                        
                if best_deal: return best_deal
                else:
                    price_matches = re.findall(r'\$(\d+(?:\.\d{2})?)', html_text)
                    valid_prices = [float(p) for p in price_matches if 40 < float(p) < 1500]
                    if valid_prices:
                        return f"🛒 **{site['name']}**: от **${min(valid_prices):.2f}**\n🔗 [Открыть поиск]({display_url})"
    except Exception as e: print(f"Ошибка поиска на {site['name']}: {e}")
    return None

async def perform_search(query: str):
    tasks = [fetch_site_search(site, query) for site in SEARCH_SITES]
    results = await asyncio.gather(*tasks)
    clean_results = [r for r in results if r is not None]
    clean_results.sort(key=lambda x: "СКИДКА" not in x)
    return clean_results

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить ссылку"), KeyboardButton(text="📋 Мой список")],
            [KeyboardButton(text="🔍 Поиск скидок")]
        ],
        resize_keyboard=True
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Я твой персональный радар и трекер скидок.\n\nВыбери действие на клавиатуре:", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔍 Поиск скидок")
@dp.message(Command("search"))
async def start_search_flow(message: types.Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    await message.answer("🔎 **Что ищем?** (Например: *Nike P-6000 black*)\n\nЯ найду, где на это действует самая большая скидка:", parse_mode="Markdown")

@dp.message(SearchState.waiting_for_query)
async def process_search_query(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    await message.answer(f"⏳ Ищу лучшие скидки на `{query}` среди {len(SEARCH_SITES)} площадок. Это займет до 20 секунд...")
    
    results = await perform_search(query)
    
    if not results:
        await message.answer("😔 Не удалось найти скидки. Попробуйте изменить запрос.", reply_markup=get_main_keyboard())
        return
    response_text = f"🔎 **Лучшие предложения по запросу:** *{query}*\n\n" + "\n\n".join(results)
    await message.answer(response_text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=get_main_keyboard())

@dp.message(F.text == "➕ Добавить ссылку")
@dp.message(Command("track"))
async def start_tracking_flow(message: types.Message, state: FSMContext):
    await state.set_state(TrackState.waiting_for_url)
    await message.answer("🔗 **Отправь мне ссылку на вещь**, цену которой ты хочешь отслеживать:", parse_mode="Markdown")

@dp.message(TrackState.waiting_for_url, ~F.text.startswith("/"), ~F.text.in_({"📋 Мой список", "➕ Добавить ссылку", "🔍 Поиск скидок"}))
async def process_track_url(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("⚠️ Это не похоже на правильную ссылку. Попробуй ещё раз:")
        return

    user_id = message.from_user.id
    await message.answer("⏳ Сканирую карточку товара...")
    fallback_title = extract_title_from_url(url)
    price, image_url, parsed_title, parsed_brand = await fetch_product_data(url)

    raw_name = parsed_title or fallback_title
    title = clean_markdown_text(raw_name)
    brand = parsed_brand or find_matched_brand(title) or find_matched_brand(url) or "Брендовая вещь"

    try:
        res = supabase.table("tracked_items").insert({
            "user_id": user_id, "url": url, "brand": brand, "title": title, "last_price": price, "image_url": image_url
        }).execute()
        if not res.data:
            await message.answer("⚠️ Не удалось сохранить товар в базу.")
            await state.clear()
            return
    except Exception as e:
        print(f"DB error: {e}")
        await message.answer("⚠️ Ошибка базы данных.")
        await state.clear()
        return

    await state.clear()
    caption_text = f"✅ **Товар сохранен!**\n\n🏷 **Бренд:** {brand}\n📦 **Модель:** {title}\n💳 **Текущая цена:** `{price}`\n\nЯ напишу тебе в ЛС, как только цена изменится!"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Открыть товар на сайте", url=url)]])

    if image_url:
        try:
            await message.answer_photo(photo=image_url, caption=caption_text, parse_mode="Markdown", reply_markup=keyboard)
            return
        except Exception as e:
            print(f"Photo error: {e}")
    await message.answer(caption_text, parse_mode="Markdown", reply_markup=keyboard)

@dp.message(F.text == "📋 Мой список")
@dp.message(Command("list"))
async def show_tracked_list(message: types.Message):
    user_id = message.from_user.id
    res = supabase.table("tracked_items").select("*").eq("user_id", user_id).execute()
    if not res.data:
        await message.answer("📭 У тебя пока нет товаров на отслеживании.", reply_markup=get_main_keyboard())
        return
    await message.answer("📋 **Твои отслеживаемые товары:**", parse_mode="Markdown")
    for item in res.data:
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Перейти к товару", url=item["url"]),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{item['id']}")
        ]])
        brand_name = clean_markdown_text(item.get('brand') or "Одежда")
        model_name = clean_markdown_text(item.get('title') or "Товар")
        caption_text = f"🏷 **Бренд:** {brand_name}\n📦 **Модель:** {model_name}\n💳 Цена: `{item['last_price']}`"
        if item.get("image_url"):
            try:
                await message.answer_photo(photo=item.get("image_url"), caption=caption_text, parse_mode="Markdown", reply_markup=inline_kb)
                continue
            except Exception: pass
        await message.answer(caption_text, parse_mode="Markdown", reply_markup=inline_kb)

@dp.callback_query(F.data.startswith("del_"))
async def delete_tracked_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_", 1)[1]
    res = supabase.table("tracked_items").delete().eq("id", item_id).eq("user_id", callback.from_user.id).execute()
    if not res.data:
        await callback.answer("❌ Товар не найден или нет прав на удаление.", show_alert=True)
        return
    await callback.answer("Товар удален!")
    await callback.message.delete()

async def check_personal_tracker():
    while True:
        try:
            res = supabase.table("tracked_items").select("*").execute()
            if res.data:
                for item in res.data:
                    new_price, _, _, _ = await fetch_product_data(item["url"])
                    if new_price != "🔥 СМОТРЕТЬ НА САЙТЕ" and new_price != item["last_price"]:
                        inline_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 КУПИТЬ СЕЙЧАС", url=item["url"])]])
                        brand_name = clean_markdown_text(item.get('brand') or "Одежда")
                        model_name = clean_markdown_text(item.get('title') or "Товар")
                        msg_text = f"🔔 **ЦЕНА ИЗМЕНИЛАСЬ!**\n\n🏷 **Бренд:** {brand_name}\n📦 **Модель:** {model_name}\n💳 **Было:** `{item['last_price']}` ➡️ **Стало:** `{new_price}`"
                        
                        if item.get("image_url"):
                            try:
                                await bot.send_photo(chat_id=item["user_id"], photo=item["image_url"], caption=msg_text, parse_mode="Markdown", reply_markup=inline_kb)
                            except Exception:
                                await bot.send_message(chat_id=item["user_id"], text=msg_text, parse_mode="Markdown", reply_markup=inline_kb)
                        else:
                            await bot.send_message(chat_id=item["user_id"], text=msg_text, parse_mode="Markdown", reply_markup=inline_kb)
                            
                        supabase.table("tracked_items").update({"last_price": new_price}).eq("id", item["id"]).eq("user_id", item["user_id"]).execute()
        except Exception as e: print(f"⚠️ Ошибка трекера: {e}")
        await asyncio.sleep(7200)

# === ОБНОВЛЕННАЯ ФУНКЦИЯ РАДАРА (БЕЗ КЭШБЭКА) ===
async def scan_and_post():
    for source in RSS_SOURCES:
        flag = source["flag"]
        rss_url = source["url"]
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries:
                title = entry.title.strip()
                main_link = entry.link
                deal_id = getattr(entry, 'id', main_link)
                summary = getattr(entry, 'summary', '')
                
                brand = find_matched_brand(title)
                if not brand: continue
                    
                if supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute().data: continue
                if supabase.table("posted_deals").select("id").eq("title", title).execute().data: continue
                    
                price = extract_price(title, summary)
                target_link = extract_best_link(summary, main_link)
                image_url = extract_hd_image(entry, summary)
                
                # ПРОВЕРКА ПРОМОКОДА ИЗ ТЕКСТА RSS
                promo_code = extract_promo_code(f"{title} {summary}")
                
                # ГЛУБОКАЯ ПРОВЕРКА ТОВАРА
                try:
                    async with AsyncSession() as s:
                        resp = await s.get(target_link, impersonate="chrome", timeout=10)
                        if resp.status_code == 200:
                            html_text = resp.text.lower()
                            
                            if any(word in html_text for word in ['"availability":"outofstock"', 'sold out', 'out of stock', 'нет в наличии']):
                                print(f"⏩ Пропуск (нет в наличии): {title[:30]}")
                                continue
                                
                            hd_match = re.search(r'property="og:image"\s+content="([^"]+)"', html_text, re.IGNORECASE)
                            if hd_match: image_url = hd_match.group(1)
                            
                            real_price_match = re.search(r'"price"\s*:\s*"?(\d+(?:\.\d{2})?)"?', html_text, re.IGNORECASE) or re.search(r'property="product:price:amount"\s+content="(\d+(?:\.\d{2})?)"', html_text, re.IGNORECASE)
                            if real_price_match: price = f"${real_price_match.group(1)}"
                except Exception as e:
                    print(f"⚠️ Ошибка глубокой проверки: {e}")
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Купить (1 клик)", url=target_link)]])
                clean_deal_title = clean_markdown_text(title)
                
                # СБОРКА ПОСТА (Кэшбэк удален)
                post_text = (
                    f"**DROP SIGNAL | #{brand.upper().replace('&', '').replace(' ', '')}**\n"
                    f"{flag}\n\n"
                    f"**{clean_deal_title}**\n"
                    f"💳 **Цена:** {price}"
                )
                
                # Добавляем промокод, только если он найден
                if promo_code:
                    post_text += f"\n🎟 **Промокод:** `{promo_code}`"
                
                try:
                    if image_url:
                        await bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=post_text, parse_mode="Markdown", reply_markup=keyboard)
                    else:
                        await bot.send_message(chat_id=CHANNEL_ID, text=post_text, parse_mode="Markdown", disable_web_page_preview=False, reply_markup=keyboard)
                    supabase.table("posted_deals").insert({"deal_id": deal_id, "title": title, "brand": brand}).execute()
                    print(f"[Channel Radar] {flag} {brand} ({price}) | Промокод: {promo_code if promo_code else 'Нет'}: {title[:30]}")
                    await asyncio.sleep(3)
                except Exception as e: print(f"⚠️ Ошибка поста: {e}")
        except Exception as e: print(f"⚠️ Ошибка ленты {rss_url}: {e}")

async def run_radar_loop():
    while True:
        try: await scan_and_post()
        except Exception as e: print(f"⚠️ Ошибка радара: {e}")
        await asyncio.sleep(180)

async def handle_ping(request):
    return web.Response(text="Global & Personal Universal Scraping Live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    global http_session
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    await start_web_server()
    print("🚀 Глобальный радар и личный трекер запущены!")
    
    radar_task = asyncio.create_task(run_radar_loop())
    tracker_task = asyncio.create_task(check_personal_tracker())
    
    try:
        await dp.start_polling(bot)
    finally:
        print("🛑 Останавливаем бота...")
        radar_task.cancel()
        tracker_task.cancel()
        await bot.session.close()
        if http_session: await http_session.close()

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): print("Бот успешно остановлен.")
