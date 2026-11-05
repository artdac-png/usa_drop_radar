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

# Список всех RSS-источников (США + Европа)
RSS_FEEDS = [
    # Slickdeals Popular Deals
    "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+adidas+puma+calvin+levis+coach+jomashop+balance&rss=1",
    # DealNews (США)
    "https://www.dealnews.com/c196/Clothing-Accessories/?rss=1",
    # HotUKDeals (Великобритания)
    "https://www.hotukdeals.com/rss/tag/fashion",
    # Mydealz (Германия)
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
                
                # Фильтруем только интересующие нас бренды
                title_lower = title.lower()
                if not any(brand in title_lower for brand in TARGET_BRANDS):
                    continue

                brand = extract_brand(title)

                # Проверяем уникальность по базе Supabase
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
                    f"⚡️ _Проверяйте наличие нужного размера и дисконт!_\n\n"
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

                    print(f"🔥 [Multi-Radar Drop] {brand}: {title[:40]}...")
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"❌ Ошибка отправки поста: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка парсинга ленты {rss_url}: {e}")

async def handle_ping(request):
    return web.Response(text="US/EU Multi-Drop Radar is live!")

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

    print("🚀 Мульти-радар США + Европа запущен на Render!")
    print("🔎 Сканирование всех источников каждые 3 минуты...")
    
    while True:
        try:
            await scan_and_post(bot)
        except Exception as e:
            print(f"⚠️ Ошибка главного цикла: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(main())
