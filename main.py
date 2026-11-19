import asyncio, os, re, html, feedparser, aiohttp
from urllib.parse import urlparse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
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
    raise RuntimeError("❌ Ошибка: Не заданы переменные окружения!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
http_session: aiohttp.ClientSession = None
EXCHANGE_RATES = {}

class TrackState(StatesGroup):
    waiting_for_url = State()

TARGET_BRANDS = [
    "jordan", "nike", "adidas", "puma", "new balance", "under armour", "converse",
    "lacoste", "tommy hilfiger", "tommy", "calvin klein", "calvin", "guess", 
    "michael kors", "kors", "coach", "levi's", "levis", "levi", "boss", "hugo boss",
    "zara", "mango", "massimo dutti", "massimo", "h&m", "h m", "hm",
    "asics", "salomon", "solomon", "saucony", "soucone", "mizuno", "hoka",
    "on running", "onrunning", "on-running", "the north face", "north face", "tnf",
    "champs", "champs sports", "nordstrom", "nordstrom rack", "rack room", "rack room shoes",
    "footlocker", "jd sports", "zappos", "asos", "mandm", "shop simon", "joes new balance",
    "woot", "shoebacca", "macy's", "macys", "gap", "old navy"
]

EXCLUDE_STOP_WORDS = [
    "watch", "watches", "chronograph", "timepiece", "smartwatch", "quartz", "dial", "clock",
    "headphone", "headphones", "earbuds", "earphone", "headset", "speaker", 
    "laptop", "tv", "television", "monitor", "phone", "smartphone", "audio", 
    "amplifier", "soundbar", "drive", "ssd", "gpu", "cpu",
    "perfume", "fragrance", "cologne", "eau de", "parfum", "toilette",
    "giftset", "gift set", "body wash", "shower gel", "deodorant", "shampoo", "lotion", "cream", "soap", "skincare", "beauty",
    "kids", "toddler", "infant", "youth", "baby", "child", "children", 
    "boy", "boys", "boy's", "girl", "girls", "girl's",
    " gs ", " gs,", "(gs)", " gs", "ps ", " ps", " td ", " td,", "preschool", "grade school",
    "expired", "dead deal", "sold out", "over", "ended", "no longer available",
    "keyboard", "wagon", "cart", "gaming", "computer", "mouse", "toy", "tools", "kitchen", "vacuum", "printer",
    "dehumidifier", "frigidaire", "air filter", "appliance", "heater", "cooler", "fridge", "refrigerator", "washing", "dryer", "microwave", "oven", "blender"
]

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
    {"flag": "🇵🇱 [Польша / PL]", "url": "https://www.pepper.pl/rss/grupa/moda-i-akcesoria"}
]

REDDIT_RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/frugalmalefashion/.rss"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.reddit.com/r/SneakerDeals/.rss"},
    {"flag": "🇬🇧 [Великобритания / GB]", "url": "https://www.reddit.com/r/FrugalMaleFashionUK/.rss"}
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
            if brand in ["champs", "champs sports"]: return "Champs Sports"
            if brand in ["nordstrom", "nordstrom rack"]: return "Nordstrom Rack"
            if brand in ["rack room", "rack room shoes"]: return "Rack Room Shoes"
            if brand in ["woot"]: return "Woot"
            if brand in ["shoebacca"]: return "Shoebacca"
            if brand in ["macy's", "macys"]: return "Macy's"
            if brand in ["gap"]: return "Gap"
            if brand in ["old navy"]: return "Old Navy"
            return brand.title()
    if "calvin" in text_lower or "calvinklein" in text_lower: return "Calvin Klein"
    if "levi.com" in text_lower or "levi" in text_lower: return "Levi's"
    return None

async def load_exchange_rates():
    global EXCHANGE_RATES
    try:
        async with http_session.get("https://open.er-api.com/v6/latest/USD", timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("result") == "success":
                    rates = data["rates"]
                    EXCHANGE_RATES = {
                        "USD": 1.0, "GBP": rates.get("GBP", 0.79),
                        "EUR": rates.get("EUR", 0.92), "UAH": rates.get("UAH", 41.5)
                    }
                    print(f"💱 Курсы валют успешно обновлены.")
    except Exception as e: print(f"⚠️ Ошибка курсов: {e}")

def format_price_multi(price_str: str) -> str:
    if not price_str or not EXCHANGE_RATES: return price_str
    match = re.search(r'([$£€])\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*([$£€])', price_str)
    if not match: return price_str
    symbol = match.group(1) if match.group(1) else match.group(4)
    amount_str = match.group(2) if match.group(1) else match.group(3)
    amount = float(amount_str.replace(',', '.'))
    if symbol == "$": base = "USD"
    elif symbol == "£": base = "GBP"
    elif symbol == "€": base = "EUR"
    else: return price_str
    usd_amount = amount if base == "USD" else amount / EXCHANGE_RATES[base]
    gbp_amount = usd_amount * EXCHANGE_RATES["GBP"]
    eur_amount = usd_amount * EXCHANGE_RATES["EUR"]
    uah_amount = usd_amount * EXCHANGE_RATES["UAH"]
    parts = []
    if base != "USD": parts.append(f"${usd_amount:.2f}")
    if base != "GBP": parts.append(f"£{gbp_amount:.2f}")
    if base != "EUR": parts.append(f"€{eur_amount:.2f}")
    return f"{price_str} (~{' / '.join(parts)} / ~{int(uah_amount)} грн)"

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
    forum_domains = ["slickdeals.net", "mydealz.de", "chollometro.com", "hotukdeals.com", "dealabs.com", "pepper.pl", "pepper.it", "dealnews.com", "bradsdeals.com", "couponcabin.com", "reddit.com"]
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
    if not url:
        reddit_img_matches = re.findall(r'(https?://(?:i|preview|external-preview)\.redd\.it/[^\s"\'<>]+)', raw_html, re.IGNORECASE)
        if reddit_img_matches: url = reddit_img_matches[0].replace('&amp;', '&')
    if not url and raw_html:
        img_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.IGNORECASE)
        if img_matches:
            for img_url in img_matches:
                if not any(stop in img_url.lower() for stop in ["beacon", "tracking", ".gif", "1x1", "avatar", "logo"]):
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
    price, image_url, parsed_title, parsed_brand = "🔥 СМОТРЕТЬ НА САЙТЕ", (extract_levis_image(url) if "levi.com" in url else None), None, None
    try:
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome", timeout=15)
            if resp.status_code == 200:
                title_match = re.search(r'property="og:title"\s+content="([^"]+)"', resp.text, re.IGNORECASE) or re.search(r'<title>([^<]+)</title>', resp.text, re.IGNORECASE)
                if title_match: parsed_title = title_match.group(1).strip()
                img_match = re.search(r'property="og:image"\s+content="([^"]+)"', resp.text, re.IGNORECASE)
                if img_match: image_url = img_match.group(1).strip()
                price_match = re.search(r'"price"\s*:\s*"?(\d+(?:\.\d{2})?)"?', resp.text, re.IGNORECASE) or re.search(r'itemprop="price"\s+content="(\d+(?:\.\d{2})?)"', resp.text, re.IGNORECASE)
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
    if parsed_title and any(word in parsed_title.lower() for word in ["oops", "error", "access denied", "not found", "forbidden"]):
        parsed_title, image_url = None, None
    return price, image_url, parsed_title, parsed_brand

def get_main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ Добавить ссылку"), KeyboardButton(text="📋 Мой список")]], resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Я твой радар скидок.", reply_markup=get_main_keyboard())

@dp.message(F.text == "➕ Добавить ссылку")
@dp.message(Command("track"))
async def start_tracking_flow(message: types.Message, state: FSMContext):
    await state.set_state(TrackState.waiting_for_url)
    await message.answer("🔗 **Отправь мне ссылку на вещь**:", parse_mode="Markdown")

@dp.message(TrackState.waiting_for_url, ~F.text.startswith("/"), ~F.text.in_({"📋 Мой список", "➕ Добавить ссылку"}))
async def process_track_url(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("⚠️ Это не ссылка.")
        return
    await message.answer("⏳ Сканирую...")
    price, image_url, parsed_title, parsed_brand = await fetch_product_data(url)
    title = clean_markdown_text(parsed_title or extract_title_from_url(url))
    brand = parsed_brand or find_matched_brand(title) or find_matched_brand(url) or "Брендовая вещь"
    try:
        res = supabase.table("tracked_items").insert({"user_id": message.from_user.id, "url": url, "brand": brand, "title": title, "last_price": price, "image_url": image_url}).execute()
        if not res.data:
            await message.answer("⚠️ Ошибка сохранения.")
            await state.clear()
            return
    except Exception as e:
        print(f"DB error: {e}"); await message.answer("⚠️ Ошибка БД."); await state.clear(); return
    await state.clear()
    caption = f"✅ **Сохранено!**\n\n🏷 **Бренд:** {brand}\n📦 **Модель:** {title}\n💳 **Цена:** `{price}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Открыть", url=url)]])
    if image_url:
        try:
            await message.answer_photo(photo=image_url, caption=caption, parse_mode="Markdown", reply_markup=kb)
            return
        except Exception: pass
    await message.answer(caption, parse_mode="Markdown", reply_markup=kb)

@dp.message(F.text == "📋 Мой список")
@dp.message(Command("list"))
async def show_tracked_list(message: types.Message):
    res = supabase.table("tracked_items").select("*").eq("user_id", message.from_user.id).execute()
    if not res.data:
        await message.answer("📭 Список пуст.", reply_markup=get_main_keyboard())
        return
    await message.answer("📋 **Твои товары:**", parse_mode="Markdown")
    for item in res.data:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Перейти", url=item["url"]), InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{item['id']}")]])
        caption = f"🏷 **Бренд:** {clean_markdown_text(item.get('brand') or 'Одежда')}\n📦 **Модель:** {clean_markdown_text(item.get('title') or 'Товар')}\n💳 Цена: `{item['last_price']}`"
        if item.get("image_url"):
            try:
                await message.answer_photo(photo=item["image_url"], caption=caption, parse_mode="Markdown", reply_markup=kb)
                continue
            except Exception: pass
        await message.answer(caption, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("del_"))
async def delete_tracked_item(callback: types.CallbackQuery):
    res = supabase.table("tracked_items").delete().eq("id", callback.data.split("_", 1)[1]).eq("user_id", callback.from_user.id).execute()
    if not res.data: await callback.answer("❌ Нет прав.", show_alert=True); return
    await callback.answer("Удалено!"); await callback.message.delete()

async def check_personal_tracker():
    while True:
        try:
            res = supabase.table("tracked_items").select("*").execute()
            if res.data:
                for item in res.data:
                    new_price, _, _, _ = await fetch_product_data(item["url"])
                    if new_price != "🔥 СМОТРЕТЬ НА САЙТЕ" and new_price != item["last_price"]:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 КУПИТЬ", url=item["url"])]])
                        msg = f"🔔 **ЦЕНА ИЗМЕНИЛАСЬ!**\n\n🏷 {clean_markdown_text(item.get('brand'))}\n📦 {clean_markdown_text(item.get('title'))}\n💳 **Было:** `{item['last_price']}` ➡️ **Стало:** `{new_price}`"
                        try:
                            if item.get("image_url"): await bot.send_photo(chat_id=item["user_id"], photo=item["image_url"], caption=msg, parse_mode="Markdown", reply_markup=kb)
                            else: await bot.send_message(chat_id=item["user_id"], text=msg, parse_mode="Markdown", reply_markup=kb)
                        except Exception: pass
                        supabase.table("tracked_items").update({"last_price": new_price}).eq("id", item["id"]).execute()
        except Exception as e: print(f"⚠️ Трекер: {e}")
        await asyncio.sleep(7200)

async def process_entry(entry, flag):
    title, main_link, summary = entry.title.strip(), entry.link, getattr(entry, 'summary', '')
    brand = find_matched_brand(title)
    if not brand: return
    deal_id = getattr(entry, 'id', main_link)
    if supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute().data: return
    if supabase.table("posted_deals").select("id").eq("title", title).execute().data: return
    price, target_link, image_url = extract_price(title, summary), extract_best_link(summary, main_link), extract_hd_image(entry, summary)
    try:
        async with AsyncSession() as s:
            resp = await s.get(target_link, impersonate="chrome", timeout=10)
            if resp.status_code == 200:
                html_text = resp.text.lower()
                if any(w in html_text for w in ['"availability":"outofstock"', 'sold out', 'out of stock', 'нет в наличии']): return
                hd_match = re.search(r'property="og:image"\s+content="([^"]+)"', html_text, re.IGNORECASE)
                if hd_match and not any(bw in hd_match.group(1).lower() for bw in ['sale', 'banner', 'promo', 'off']): image_url = hd_match.group(1)
    except Exception: pass
    price_with_multi = format_price_multi(price)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Купить (1 клик)", url=target_link)]] + ([[InlineKeyboardButton(text="🔗 Источник", url=main_link)]] if main_link != target_link else []))
    post_text = f"**DROP SIGNAL | #{brand.upper().replace('&', '').replace(' ', '')}**\n{flag}\n\n**{clean_markdown_text(title)}**\n💳 **Цена:** {price_with_multi}"
    try:
        if image_url: await bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=post_text, parse_mode="Markdown", reply_markup=kb)
        else: await bot.send_message(chat_id=CHANNEL_ID, text=post_text + f"\n\n[​]({target_link})", parse_mode="Markdown", disable_web_page_preview=False, reply_markup=kb)
        supabase.table("posted_deals").insert({"deal_id": deal_id, "title": title, "brand": brand}).execute()
        await asyncio.sleep(3)
    except Exception as e: print(f"⚠️ Пост: {e}")

async def fetch_feed(source):
    try:
        feed = await asyncio.to_thread(feedparser.parse, source["url"])
        return feed.entries, source["flag"]
    except Exception: return [], source["flag"]

async def scan_and_post(sources):
    results = await asyncio.gather(*[fetch_feed(source) for source in sources])
    for entries, flag in results:
        for entry in entries: await process_entry(entry, flag)

async def run_radar_standard():
    while True:
        try: await scan_and_post(STANDARD_RSS_SOURCES)
        except Exception as e: print(f"⚠️ Радар: {e}")
        await asyncio.sleep(180)

async def run_radar_reddit():
    while True:
        try: await scan_and_post(REDDIT_RSS_SOURCES)
        except Exception as e: print(f"⚠️ Reddit: {e}")
        await asyncio.sleep(300)

async def update_rates_loop():
    while True:
        await load_exchange_rates()
        await asyncio.sleep(43200)

async def handle_ping(request): return web.Response(text="Live!")

async def start_web_server():
    app = web.Application(); app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))); await site.start()

async def main():
    global http_session
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    await load_exchange_rates(); await start_web_server()
    print("🚀 Бот запущен!")
    tasks = [asyncio.create_task(run_radar_standard()), asyncio.create_task(run_radar_reddit()), asyncio.create_task(check_personal_tracker()), asyncio.create_task(update_rates_loop())]
    try: await dp.start_polling(bot)
    finally:
        for t in tasks: t.cancel()
        await bot.session.close()
        if http_session: await http_session.close()

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): print("Бот остановлен.")
