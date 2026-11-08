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
        url = re.sub(r'_(?:thumb|small|medium)\.', '.', url)
    elif any(domain in url for domain in ["pepper.com", "static-pepper", "hotukdeals", "mydealz", "chollometro", "dealabs"]):
        url = re.sub(r'/re/\d+x\d+/qt/\d+/', '/', url)

    return url

# --- ИЗОЛИРОВАННЫЙ МОДУЛЬ ЛИЧНОГО ТРЕКИНГА ССЫЛОК ---
async def fetch_page_price(url: str) -> str:
    """Безопасно запрашивает страницу товара и пытается найти цену."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    price_pattern = r'(\$\d+(?:\.\d{2})?|\b\d+(?:[.,]\d{1,2})?\s*€|€\s*\d+(?:[.,]\d{1,2})?|£\d+(?:\.\d{2})?)'
                    matches = re.findall(price_pattern, html)
                    if matches:
                        return matches[0]
    except Exception as e:
        print(f"⚠️ Ошибка отслеживания ссылки {url}: {e}")
    return "См. на сайте"

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой персональный радар скидок.\n\n"
        "📍 **Как отслеживать конкретные товары:**\n"
        "Отправь мне команду `/track ССЫЛКА` и я начну следить за ценой!\n\n"
        "📋 Команды:\n"
        "`/track https://...` — добавить товар\n"
        "`/list` — мои товары на отслеживании\n"
        "`/untrack ID` — удалить товар"
    )

@dp.message(Command("track"))
async def cmd_track(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажи ссылку на товар: `/track https://...`", parse_mode="Markdown")
        return

    url = args[1].strip()
    user_id = message.from_user.id

    price = await fetch_page_price(url)
    brand = find_matched_brand(url) or "Персональный трекинг"
    title = f"Товар {urlparse(url).netloc}"

    supabase.table("tracked_items").insert({
        "user_id": user_id,
        "url": url,
        "brand": brand,
        "title": title,
        "last_price": price
    }).execute()

    await message.answer(
        f"✅ **Товар добавлен на отслеживание!**\n\n"
        f"🔗 [Перейти к товару]({url})\n"
        f"🏷 **Текущая цена:** `{price}`\n\n"
        f"_Я напишу тебе в ЛС, как только цена изменится!_",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    user_id = message.from_user.id
    res = supabase.table("tracked_items").select("*").eq("user_id", user_id).execute()
    
    if not res.data:
        await message.answer("📭 У тебя пока нет товаров на отслеживании.")
        return

    text = "📋 **Твои отслеживаемые товары:**\n\n"
    for item in res.data:
        text += f"🔹 **ID:** `{item['id']}` | [{item['brand']}]({item['url']})\n   🏷 Цена: `{item['last_price']}`\n\n"

    text += "Удалить: `/untrack ID`"
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)

@dp.message(Command("untrack"))
async def cmd_untrack(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажи ID товара: `/untrack ID`")
        return
    item_id = args[1].strip()
    supabase.table("tracked_items").delete().eq("id", item_id).execute()
    await message.answer(f"🗑 Товар #{item_id} удален из отслеживания.")

async def check_personal_tracker():
    """Фоновая задача: раз в 2 часа проверяет твои персональные ссылки."""
    while True:
        try:
            res = supabase.table("tracked_items").select("*").execute()
            if res.data:
                for item in res.data:
                    new_price = await fetch_page_price(item["url"])
                    if new_price != "См. на сайте" and new_price != item["last_price"]:
                        await bot.send_message(
                            chat_id=item["user_id"],
                            text=(
                                f"🔔 **ЦЕНА ИЗМЕНИЛАСЬ!**\n\n"
                                f"📦 [{item['brand']}]({item['url']})\n"
                                f"🏷 **Было:** `{item['last_price']}` ➡️ **Стало:** `{new_price}`"
                            ),
                            parse_mode="Markdown"
                        )
                        supabase.table("tracked_items").update({"last_price": new_price}).eq("id", item["id"]).execute()
        except Exception as e:
            print(f"⚠️ Ошибка фонового трекера: {e}")
            
        await asyncio.sleep(7200) # Проверка 1 раз в 2 часа

# --- ГЛОБАЛЬНЫЙ СКАНИРОВАТЕЛЬ КАНАЛА (РАБОТАЕТ КАК И РАНЬШЕ) ---
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
                if not brand:
                    continue

                res_id = supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute()
                if res_id.data:
                    continue

                res_title = supabase.table("posted_deals").select("id").eq("title", title).execute()
                if res_title.data:
                    continue

                price = extract_price(title, summary)
                target_link = extract_best_link(summary, main_link)
                cashback_info = CASHBACK_MAP.get(brand, "TopCashback / Rakuten / iGraal / Beruby")
                image_url = extract_hd_image(entry, summary)

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛒 КУПИТЬ (1 КЛИК)", url=target_link)]
                ])

                post_text = (
                    f"🚨 **DROP SIGNAL | #{brand.upper().replace('&', '').replace(' ', '')}**\n"
                    f"{flag}\n\n"
                    f"📦 **{title}**\n"
                    f"🏷 **Цена:** `{price}`\n"
                    f"💳 **Кэшбэк:** `{cashback_info}`"
                )

                try:
                    if image_url:
                        await bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=image_url,
                            caption=post_text,
                            parse_mode="Markdown",
                            reply_markup=keyboard
                        )
                    else:
                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=post_text,
                            parse_mode="Markdown",
                            disable_web_page_preview=False,
                            reply_markup=keyboard
                        )

                    supabase.table("posted_deals").insert({
                        "deal_id": deal_id,
                        "title": title,
                        "brand": brand
                    }).execute()

                    print(f"🔥 [Channel Radar] {flag} {brand} ({price}): {title[:30]}...")
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"❌ Ошибка отправки поста: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка ленты {rss_url}: {e}")

async def run_radar_loop():
    while True:
        try:
            await scan_and_post()
        except Exception as e:
            print(f"⚠️ Ошибка радара: {e}")
        await asyncio.sleep(180)

async def handle_ping(request):
    return web.Response(text="Global & Personal Radar Live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    print("🚀 Глобальный радар и личный трекер запущены!")
    
    # Запуск всех фоновых задач без блокировки
    asyncio.create_task(run_radar_loop())
    asyncio.create_task(check_personal_tracker())
    
    # Запуск обработки личных команд в Telegram
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
