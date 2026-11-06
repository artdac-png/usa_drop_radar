import asyncio
import os
import re
import feedparser
from aiogram import Bot
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

TARGET_BRANDS = [
    "jordan", "nike", "adidas", "puma", "new balance", "under armour", "converse",
    "lacoste", "tommy hilfiger", "tommy", "calvin klein", "calvin", "guess", 
    "michael kors", "kors", "coach", "levi's", "levis",
    "zara", "mango", "h&m", "h m", "hm",
    "zappos", "jomashop", "6pm"
]

CASHBACK_MAP = {
    "Jordan": "TopCashback / Rakuten / Beruby (до 8-10%)",
    "Nike": "TopCashback / Beruby / Rakuten (до 8-10%)",
    "Adidas": "TopCashback / Beruby (до 6-8%)",
    "Puma": "TopCashback / iGraal (до 10%)",
    "New balance": "TopCashback / Rakuten (до 5%)",
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
    "Zara": "TopCashback / Beruby / iGraal (до 4-5%)",
    "Mango": "TopCashback / iGraal / Beruby (до 6-8%)",
    "H&m": "TopCashback / Rakuten / iGraal (до 4-6%)",
    "Jomashop": "TopCashback / Rakuten (до 4%)",
    "Zappos": "TopCashback / Rakuten (до 5%)",
    "6pm": "TopCashback / Rakuten (до 4%)"
}

RSS_SOURCES = [
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+jordan+adidas+lacoste+tommy+calvin+guess+converse+zara+mango&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.dealnews.com/c196/Clothing-Accessories/?rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.bradsdeals.com/rss/fashion"},
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
    for brand in TARGET_BRANDS:
        pattern = r'\b' + re.escape(brand) + r'\b'
        if re.search(pattern, title_lower):
            if brand in ["h&m", "h m", "hm"]:
                return "H&M"
            return brand.capitalize()
    return None

def extract_price(title: str, summary: str) -> str:
    price_pattern = r'(\$\d+(?:\.\d{2})?|\b\d+(?:[.,]\d{1,2})?\s*€|€\s*\d+(?:[.,]\d{1,2})?|£\d+(?:\.\d{2})?)'
    match_title = re.findall(price_pattern, title)
    if match_title:
        return match_title[0]
        
    if summary:
        match_summary = re.findall(price_pattern, summary)
        if match_summary:
            return match_summary[0]
            
    return "См. на сайте"

def extract_promo_code(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    pattern = r'(?:code|coupon|gutschein|código|промокод|promo|use|w/|with)\s*[:\-]?\s*([A-Za-z0-9_\-]{3,15})'
    matches = re.findall(pattern, text, re.IGNORECASE)
    
    stop_words = [
        "the", "for", "and", "mit", "con", "off", "free", "shipping", "sale", 
        "this", "your", "from", "member", "new", "sign", "prime", "delivery", 
        "orders", "order", "with", "only"
    ]
    
    for code in matches:
        code_clean = code.strip().upper()
        if code_clean.lower() not in stop_words and len(code_clean) >= 3:
            if any(char.isdigit() for char in code_clean) or code_clean.isupper():
                return code_clean
                
    return None

def extract_store_buy_link(summary: str, forum_link: str) -> str:
    if not summary:
        return forum_link
    
    urls = re.findall(r'href="(https?://[^"]+)"', summary, re.IGNORECASE)
    
    for url in urls:
        if any(keyword in url.lower() for keyword in ["buyfromdeal", "sdtid", "visitthread", "checkout", "out/"]):
            return url
            
    forum_domains = [
        "slickdeals.net/f/", "mydealz.de/deals/", "chollometro.com/ofertas/", 
        "hotukdeals.com/deals/", "dealabs.com/bons-plans/", "pepper.pl/promocje/"
    ]
    for url in urls:
        if not any(domain in url.lower() for domain in forum_domains):
            return url
            
    return forum_link

def extract_hd_image(summary: str) -> str:
    if not summary:
        return None
        
    img_matches = re.findall(r'(?:src|data-src)="([^"]+\.(?:jpg|png|jpeg)[^"]*)"', summary, re.IGNORECASE)
    if not img_matches:
        return None

    url = img_matches[0]
    url = re.sub(r'threads/raw/default/\d+x\d+/\w+/\d+/', 'threads/raw/default/', url)
    url = re.sub(r'threads/thread_full_custom/\d+x\d+/\w+/\d+/', 'threads/raw/default/', url)
    url = re.sub(r'/\d+x\d+/', '/800x800/', url)
    url = re.sub(r'_thumb\.', '.', url)
    url = re.sub(r'_small\.', '.', url)

    return url

async def scan_and_post(bot: Bot):
    for source in RSS_SOURCES:
        flag = source["flag"]
        rss_url = source["url"]
        try:
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries:
                title = entry.title
                forum_link = entry.link
                deal_id = getattr(entry, 'id', forum_link)
                summary = getattr(entry, 'summary', '')
                
                brand = find_matched_brand(title)
                if not brand:
                    continue

                price = extract_price(title, summary)
                promo_code = extract_promo_code(title, summary)
                store_link = extract_store_buy_link(summary, forum_link)
                cashback_info = CASHBACK_MAP.get(brand, "TopCashback / Rakuten / iGraal / Beruby")

                res = supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute()
                if res.data:
                    continue

                image_url = extract_hd_image(summary)
                promo_block = f"🎟 **Промокод:** `{promo_code}`\n" if promo_code else ""

                # Создаем Inline-кнопки под сообщением
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛒 В магазин (1 клик)", url=store_link)],
                    [InlineKeyboardButton(text="💬 Обсуждение на форуме", url=forum_link)]
                ])

                post_text = (
                    f"🚨 **DROP SIGNAL | #{brand.upper().replace('&', '')}**\n"
                    f"{flag}\n\n"
                    f"📦 **{title}**\n"
                    f"🏷 **Цена:** `{price}`\n"
                    f"{promo_block}"
                    f"⚡️ _Проверяйте наличие нужного размера и дисконт!_\n"
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

                    print(f"🔥 [Inline Buttons Radar] {flag} {brand} ({price}): {title[:30]}...")
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"❌ Ошибка отправки поста: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка ленты {rss_url}: {e}")

async def handle_ping(request):
    return web.Response(text="Global Multi-Brand Inline Keyboard Radar is live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    bot = Bot(token=BOT_TOKEN)
    await start_web_server()

    print("🚀 Глобальный радар с интерактивными кнопками запущен!")
    
    while True:
        try:
            await scan_and_post(bot)
        except Exception as e:
            print(f"⚠️ Ошибка главного цикла: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(main())
