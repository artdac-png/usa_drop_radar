import asyncio
import os
import re
import feedparser
from aiogram import Bot
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
    """Ищет промокод по ключевым словам на разных языках (code, coupon, gutschein, código, промокод)."""
    text = f"{title} {summary}"
    # Ищем конструкции вида: code PROMO20, coupon: SAVE15, gutschein: NIKE20, código EXTRA10
    pattern = r'(?:code|coupon|gutschein|código|промокод|code:)\s*[:\-]?\s*([A-Za-z0-9_\-]+)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        code = match.group(1).strip()
        # Исключаем ложные короткие слова
        if len(code) >= 3 and not code.lower() in ["the", "for", "and", "mit", "con"]:
            return code.upper()
    return None

async def scan_and_post(bot: Bot):
    for source in RSS_SOURCES:
        flag = source["flag"]
        rss_url = source["url"]
        try:
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries:
                title = entry.title
                link = entry.link
                deal_id = getattr(entry, 'id', link)
                summary = getattr(entry, 'summary', '')
                
                brand = find_matched_brand(title)
                if not brand:
                    continue

                price = extract_price(title, summary)
                promo_code = extract_promo_code(title, summary)
                cashback_info = CASHBACK_MAP.get(brand, "TopCashback / Rakuten / iGraal / Beruby")

                res = supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute()
                if res.data:
                    continue

                image_url = None
                if summary:
                    img_match = re.search(r'src="(https?://[^"]+\.(?:jpg|png|jpeg))"', summary)
                    if img_match:
                        image_url = img_match.group(1)

                # Формируем блок промокода, если он найден
                promo_block = f"🎟 **Промокод:** `{promo_code}`\n" if promo_code else ""

                post_text = (
                    f"🚨 **DROP SIGNAL | #{brand.upper().replace('&', '')}**\n"
                    f"{flag}\n\n"
                    f"📦 **{title}**\n"
                    f"🏷 **Цена:** `{price}`\n"
                    f"{promo_block}\n"
                    f"⚡️ _Проверяйте наличие нужного размера и дисконт!_\n"
                    f"💳 **Кэшбэк:** `{cashback_info}`\n\n"
                    f"🔗 [КУПИТЬ В 1 КЛИК]({link})"
                )

                try:
                    if image_url:
                        await bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=image_url,
                            caption=post_text,
                            parse_mode="Markdown"
                        )
                    else:
                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=post_text,
                            parse_mode="Markdown",
                            disable_web_page_preview=False
                        )

                    supabase.table("posted_deals").insert({
                        "deal_id": deal_id,
                        "title": title,
                        "brand": brand
                    }).execute()

                    print(f"🔥 [Promo Radar] {flag} {brand} ({price}) Code: {promo_code}...")
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"❌ Ошибка отправки поста: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка ленты {rss_url}: {e}")

async def handle_ping(request):
    return web.Response(text="Global Multi-Brand Radar with Promo Codes is live!")

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

    print("🚀 Глобальный радар с автопоиском промокодов запущен!")
    
    while True:
        try:
            await scan_and_post(bot)
        except Exception as e:
            print(f"⚠️ Ошибка главного цикла: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(main())
