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
    "nike", "adidas", "puma", "calvin klein", "levi's", "levis", 
    "new balance", "coach", "jordan", "under armour", "zappos", "jomashop", "6pm"
]

# Справочник лучших кэшбэк-платформ под бренды и магазины
CASHBACK_MAP = {
    "Nike": "TopCashback US (до 8-10%) | Rakuten (до 5%)",
    "Adidas": "TopCashback US (до 6-8%) | Beruby (до 5%)",
    "Puma": "TopCashback US (до 10%) | Rakuten (до 6%)",
    "Calvin klein": "TopCashback US (до 7%) | Rakuten (до 4%)",
    "Levi's": "TopCashback US (до 8%) | iGraal (до 5%)",
    "Levis": "TopCashback US (до 8%) | iGraal (den 5%)",
    "New balance": "TopCashback US (до 5%) | Rakuten (до 4%)",
    "Coach": "TopCashback US (до 6%) | Rakuten (до 4%)",
    "Jomashop": "TopCashback US (до 4%) | Rakuten (до 3%)",
    "Zappos": "TopCashback US (до 5%) | Rakuten (до 3%)",
    "6pm": "TopCashback US (до 4%) | Rakuten (до 2.5%)"
}

RSS_FEEDS = [
    "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+adidas+puma+calvin+levis+coach+jomashop+balance&rss=1",
    "https://www.dealnews.com/c196/Clothing-Accessories/?rss=1",
    "https://www.hotukdeals.com/rss/tag/fashion",
    "https://www.mydealz.de/rss/gruppe/fashion"
]

def extract_brand(title: str) -> str:
    title_lower = title.lower()
    for brand in TARGET_BRANDS:
        if brand in title_lower:
            return brand.capitalize()
    return "US/EU Brand"

async def scan_and_post(bot: Bot):
    for rss_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries:
                title = entry.title
                link = entry.link
                deal_id = getattr(entry, 'id', link)
                
                title_lower = title.lower()
                if not any(brand in title_lower for brand in TARGET_BRANDS):
                    continue

                brand = extract_brand(title)
                cashback_info = CASHBACK_MAP.get(brand, "TopCashback US / Rakuten")

                res = supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute()
                if res.data:
                    continue

                image_url = None
                if hasattr(entry, 'summary'):
                    img_match = re.search(r'src="(https?://[^"]+\.(?:jpg|png|jpeg))"', entry.summary)
                    if img_match:
                        image_url = img_match.group(1)

                post_text = (
                    f"🚨 **DROP SIGNAL | #{brand.upper()}**\n\n"
                    f"📦 **{title}**\n\n"
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

                    print(f"🔥 [Drop Signal with Cashback] {brand}: {title[:40]}...")
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"❌ Ошибка отправки поста: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка парсинга ленты {rss_url}: {e}")

async def handle_ping(request):
    return web.Response(text="US/EU Cashback Radar is live!")

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

    print("🚀 Радар с кэшбэк-подсказками запущен!")
    
    while True:
        try:
            await scan_and_post(bot)
        except Exception as e:
            print(f"⚠️ Ошибка главного цикла: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(main())
