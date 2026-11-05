import asyncio
import os
import re
import feedparser
from aiogram import Bot
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TARGET_BRANDS = [
    "nike", "adidas", "puma", "calvin klein", "levi's", "levis", 
    "new balance", "coach", "jordan", "under armour", "zappos", "jomashop"
]

def extract_brand(title: str) -> str:
    title_lower = title.lower()
    for brand in TARGET_BRANDS:
        if brand in title_lower:
            return brand.capitalize()
    return "US Brand"

async def scan_and_post(bot: Bot):
    search_query = "+".join(["nike", "adidas", "puma", "calvin", "levis", "coach", "jomashop", "balance"])
    rss_url = f"https://slickdeals.net/newsearch.php?mode=popdeals&q={search_query}&rss=1"
    
    feed = feedparser.parse(rss_url)
    
    for entry in feed.entries:
        title = entry.title
        link = entry.link
        deal_id = getattr(entry, 'id', link)
        brand = extract_brand(title)

        # Проверка дубликатов в Supabase
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

            # Сохранение в Supabase
            supabase.table("posted_deals").insert({
                "deal_id": deal_id,
                "title": title,
                "brand": brand
            }).execute()

            print(f"🔥 [Drop Signal] {brand}: {title[:40]}...")
            await asyncio.sleep(3)

        except Exception as e:
            print(f"❌ Ошибка отправки поста: {e}")

async def main():
    bot = Bot(token=BOT_TOKEN)

    print("🚀 Радар молниеносного поиска скидок в США запущен!")
    print("🔎 Проверка каждые 3 минуты...")
    
    while True:
        try:
            await scan_and_post(bot)
        except Exception as e:
            print(f"⚠️ Ошибка цикла: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(main())