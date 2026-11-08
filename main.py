import asyncio
import os
import re
import feedparser
from urllib.parse import urlparse
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
    "michael kors", "kors", "coach", "levi's", "levis", "levi",
    "zara", "mango", "massimo dutti", "massimo", "h&m", "h m", "hm",
    "zappos", "jomashop", "6pm", "foot locker", "footlocker",
    "jd sports", "jdsports", "jd",
    "nordstrom rack", "rack", "finish line", "finishline", 
    "champs sports", "champs", "dicks sporting goods", "dicks",
    "joes new balance", "joesnewbalanceoutlet", "joe's new balance",
    "brandalley", "brand alley", "sports direct", "sportsdirect",
    "solebox", "end clothing", "end.", "endclothing", "decathlon",
    "asics", "salomon", "solomon", "saucony", "soucone", "mizuno", "hoka",
    "on running", "onrunning", "on-running"
]

CASHBACK_MAP = {
    "Jordan": "TopCashback / Rakuten / Beruby (до 8-10%)",
    "Nike": "TopCashback / Beruby / Rakuten (до 8-10%)",
    "Adidas": "TopCashback / Beruby (до 6-8%)",
    "Puma": "TopCashback / iGraal (до 10%)",
    "New balance": "TopCashback / Rakuten (до 5%)",
    "Joes new balance": "TopCashback / Rakuten / BeFrugal (до 6-8%)",
    "Joesnewbalanceoutlet": "TopCashback / Rakuten / BeFrugal (до 6-8%)",
    "Joe's new balance": "TopCashback / Rakuten / BeFrugal (до 6-8%)",
    "Brandalley": "TopCashback UK / Quidco (до 6-9%)",
    "Brand alley": "TopCashback UK / Quidco (до 6-9%)",
    "Sports direct": "TopCashback UK / Quidco (до 4-7%)",
    "Sportsdirect": "TopCashback UK / Quidco (до 4-7%)",
    "Solebox": "TopCashback DE / iGraal (до 5-8%)",
    "End clothing": "TopCashback UK / Quidco (до 6-8%)",
    "End.": "TopCashback UK / Quidco (до 6-8%)",
    "Endclothing": "TopCashback UK / Quidco (до 6-8%)",
    "Decathlon": "TopCashback / iGraal / Beruby (до 4-6%)",
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
    "Zara": "TopCashback / Beruby / iGraal (до 4-5%)",
    "Mango": "TopCashback / iGraal / Beruby (до 6-8%)",
    "Massimo dutti": "TopCashback / Beruby / iGraal (до 5-7%)",
    "Massimo": "TopCashback / Beruby / iGraal (до 5-7%)",
    "H&m": "TopCashback / Rakuten / iGraal (до 4-6%)",
    "Jomashop": "TopCashback / Rakuten (до 4%)",
    "Zappos": "TopCashback / Rakuten (до 5%)",
    "6pm": "TopCashback / Rakuten (до 4%)",
    "Foot locker": "TopCashback / Rakuten / iGraal (до 4-6%)",
    "Footlocker": "TopCashback / Rakuten / iGraal (до 4-6%)",
    "Jd sports": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Jdsports": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Jd": "TopCashback / Rakuten / iGraal (до 6-8%)",
    "Nordstrom rack": "TopCashback / Rakuten (до 4-6%)",
    "Rack": "TopCashback / Rakuten (до 4-6%)",
    "Finish line": "TopCashback / Rakuten (до 5-7%)",
    "Finishline": "TopCashback / Rakuten (до 5-7%)",
    "Champs sports": "TopCashback / Rakuten (до 4-5%)",
    "Champs": "TopCashback / Rakuten (до 4-5%)",
    "Dicks sporting goods": "TopCashback / Rakuten (до 4-6%)",
    "Dicks": "TopCashback / Rakuten (до 4-6%)",
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
    # --- США: Расширенный фид точечных скидок + Купонные баннеры ---
    {"flag": "🇺🇸 [США / US]", "url": "https://slickdeals.net/newsearch.php?mode=popdeals&q=nike+jordan+adidas+lacoste+tommy+calvin+asics+salomon+saucony+mizuno+hoka+onrunning+levis+joesnewbalance+zappos+jdsports+6pm+jomashop+rack+finishline+champs+dicks&rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.dealnews.com/c196/Clothing-Accessories/?rss=1"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.bradsdeals.com/rss/fashion"},
    {"flag": "🇺🇸 [США / US]", "url": "https://www.bradsdeals.com/rss/coupons"},
    {"flag": "🇺🇸 [США / US]", "url": "https://rss.couponcabin.com/coupons/clothing-and-apparel.xml"},
    
    # --- Европа (включая Sports Direct, Solebox, END. Clothing, Decathlon, JD/Foot Locker EU) ---
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

TECH_STOP_WORDS = [
    "headphone", "headphones", "earbuds", "earphone", "headset", "speaker", 
    "laptop", "tv", "television", "monitor", "phone", "smartphone", "audio", 
    "amplifier", "soundbar", "drive", "ssd", "gpu", "cpu"
]

def find_matched_brand(title: str) -> str:
    title_lower = title.lower()
    
    if any(stop in title_lower for stop in TECH_STOP_WORDS):
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
            if brand in ["foot locker", "footlocker"]:
                return "Foot Locker"
            if brand in ["jd sports", "jdsports", "jd"]:
                return "JD Sports"
            if brand in ["nordstrom rack", "rack"]:
                return "Nordstrom Rack"
            if brand in ["finish line", "finishline"]:
                return "Finish Line"
            if brand in ["champs sports", "champs"]:
                return "Champs Sports"
            if brand in ["dicks sporting goods", "dicks"]:
                return "Dick's Sporting Goods"
            if brand in ["joes new balance", "joesnewbalanceoutlet", "joe's new balance"]:
                return "New Balance"
            if brand in ["levi's", "levis", "levi"]:
                return "Levi's"
            if brand in ["brandalley", "brand alley"]:
                return "BrandAlley"
            if brand in ["sports direct", "sportsdirect"]:
                return "Sports Direct"
            if brand in ["end clothing", "end.", "endclothing"]:
                return "END. Clothing"
            if brand in ["solebox"]:
                return "Solebox"
            if brand in ["decathlon"]:
                return "Decathlon"
            return brand.capitalize()
    return None

def extract_price(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    
    # 1. Комплексные акции ("Buy 2 for $80", "2 for $80")
    bundle_match = re.findall(r'(\b(?:buy\s*)?\d+\s*(?:for|por)\s*[\$\b\d\.\,€£\s]+(?:\$\d+|\d+€|\d+£)?)', text, re.IGNORECASE)
    if bundle_match:
        return bundle_match[0].strip()

    # 2. Одиночная цена ($29.99, £15, 20€)
    price_pattern = r'(\$\d+(?:\.\d{2})?|\b\d+(?:[.,]\d{1,2})?\s*€|€\s*\d+(?:[.,]\d{1,2})?|£\d+(?:\.\d{2})?)'
    match_title = re.findall(price_pattern, title)
    if match_title:
        return match_title[0]
        
    if summary:
        match_summary = re.findall(price_pattern, summary)
        if match_summary:
            return match_summary[0]

    # 3. Баннеры процентных скидок (Extra 30% Off, Up to 65% Off)
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
            
    forum_domains = [
        "slickdeals.net", "mydealz.de", "chollometro.com", 
        "hotukdeals.com", "dealabs.com", "pepper.pl", "pepper.it", "dealnews.com", "bradsdeals.com", "couponcabin.com"
    ]
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

async def scan_and_post(bot: Bot):
    for source in RSS_SOURCES:
        flag = source["flag"]
        rss_url = source["url"]
        try:
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries:
                title = entry.title
                main_link = entry.link
                deal_id = getattr(entry, 'id', main_link)
                summary = getattr(entry, 'summary', '')
                
                brand = find_matched_brand(title)
                if not brand:
                    continue

                price = extract_price(title, summary)
                target_link = extract_best_link(summary, main_link)
                cashback_info = CASHBACK_MAP.get(brand, "TopCashback / Rakuten / iGraal / Beruby")

                res = supabase.table("posted_deals").select("id").eq("deal_id", deal_id).execute()
                if res.data:
                    continue

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

                    print(f"🔥 [EU Expansion Live] {flag} {brand} ({price}): {title[:30]}...")
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"❌ Ошибка отправки поста: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка ленты {rss_url}: {e}")

async def handle_ping(request):
    return web.Response(text="Global Multi-Brand Clean Radar Live!")

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

    print("🚀 Глобальный радар запущен!")
    
    while True:
        try:
            await scan_and_post(bot)
        except Exception as e:
            print(f"⚠️ Ошибка главного цикла: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(main())
