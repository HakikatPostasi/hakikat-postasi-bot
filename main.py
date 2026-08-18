# -*- coding: utf-8 -*-
import os
import time
import json
import requests
import feedparser
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from http.server import HTTPServer, BaseHTTPRequestHandler

# ================= KİŞİSEL BİLGİLER =================
TELEGRAM_BOT_TOKEN = "8593635234:AAFbi0rgEseK3g2nWPf2WlqASNe4eFoSUVE"
TELEGRAM_CHAT_ID = "8248123182"

TOKEN_FILE = "facebook_token.txt"
PAYLASILANLAR_FILE = "paylasilanlar.txt"

RSS_URLS = [
    "https://www.aa.com.tr/tr/rss/default?cat=guncel",
    "https://www.trthaber.com/guncel_articles.rss"
]

pending_news = {}

# ================= YARDIMCI FONKSİYONLAR =================

def load_facebook_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get("FACEBOOK_TOKEN", "")

def load_posted():
    if not os.path.exists(PAYLASILANLAR_FILE):
        return set()
    with open(PAYLASILANLAR_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(link):
    with open(PAYLASILANLAR_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

def get_page_id(token):
    url = f"https://graph.facebook.com/v26.0/me?access_token={token}"
    r = requests.get(url).json()
    return r.get("id")

def share_to_facebook(image_path, caption):
    token = load_facebook_token()
    page_id = get_page_id(token)
    url = f"https://graph.facebook.com/v26.0/{page_id}/photos"
    with open(image_path, "rb") as img_file:
        files = {"source": img_file}
        payload = {"caption": caption, "access_token": token}
        res = requests.post(url, files=files, data=payload).json()
    return res

def extract_image_url(entry):
    if "media_content" in entry and len(entry.media_content) > 0:
        return entry.media_content[0].get("url")
    if "links" in entry:
        for l in entry.links:
            if l.get("type", "").startswith("image/"):
                return l.get("href")
    if "enclosures" in entry and len(entry.enclosures) > 0:
        return entry.enclosures[0].get("href")
    return None

def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines = []
    curr = []
    for w in words:
        curr.append(w)
        bbox = draw.textbbox((0, 0), " ".join(curr), font=font)
        if (bbox[2] - bbox[0]) > max_width:
            curr.pop()
            lines.append(" ".join(curr))
            curr = [w]
    if curr:
        lines.append(" ".join(curr))
    return lines

def create_news_card(image_url, title, summary, source_name="Haber Merkezi", output_filename="temp_card.jpg"):
    card_w, card_h = 1080, 1080
    card = Image.new("RGB", (card_w, card_h), (18, 18, 20))
    draw = ImageDraw.Draw(card)
    img_h = 600

    if image_url:
        try:
            resp = requests.get(image_url, timeout=10)
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img_ratio = img.width / img.height
            target_ratio = card_w / img_h
            if img_ratio > target_ratio:
                new_w = int(img_h * img_ratio)
                img = img.resize((new_w, img_h), Image.Resampling.LANCZOS)
                crop_x = (new_w - card_w) // 2
                img = img.crop((crop_x, 0, crop_x + card_w, img_h))
            else:
                new_h = int(card_w / img_ratio)
                img = img.resize((card_w, new_h), Image.Resampling.LANCZOS)
                crop_y = (new_h - img_h) // 2
                img = img.crop((0, crop_y, card_w, crop_y + img_h))
            card.paste(img, (0, 0))
        except Exception:
            pass

    # Kırmızı Son Dakika Rozeti
    draw.rectangle([(50, img_h + 30), (250, img_h + 75)], fill=(200, 30, 30))
    
    # Sunucu uyumlu varsayılan font kontrolü
    try:
        font_badge = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
        font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
        font_summary = ImageFont.truetype("DejaVuSans.ttf", 28)
        font_footer = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
    except Exception:
        try:
            font_badge = ImageFont.truetype("arialbd.ttf", 26)
            font_title = ImageFont.truetype("arialbd.ttf", 40)
            font_summary = ImageFont.truetype("arial.ttf", 28)
            font_footer = ImageFont.truetype("arialbd.ttf", 24)
        except Exception:
            font_badge = font_title = font_summary = font_footer = ImageFont.load_default()

    draw.text((65, img_h + 38), "SON DAKİKA", fill=(255, 255, 255), font=font_badge)

    title_lines = wrap_text(title, font_title, 980, draw)[:3]
    y_text = img_h + 95
    for line in title_lines:
        draw.text((50, y_text), line, fill=(255, 255, 255), font=font_title)
        y_text += 50

    clean_summary = summary.replace("<p>", "").replace("</p>", "").strip()
    summary_lines = wrap_text(clean_summary, font_summary, 980, draw)[:3]
    y_text += 15
    for line in summary_lines:
        draw.text((50, y_text), line, fill=(185, 185, 185), font=font_summary)
        y_text += 38

    draw.line([(50, 1010), (1030, 1010)], fill=(50, 50, 50), width=2)
    draw.text((50, 1030), "HAKİKAT POSTASI", fill=(220, 40, 40), font=font_footer)
    draw.text((800, 1030), f"Kaynak: {source_name}", fill=(120, 120, 120), font=font_footer)

    card.save(output_filename, quality=95)
    return output_filename

# ================= TELEGRAM GÖNDERİMİ =================

def send_telegram_card(news_id, card_path, title, summary):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Facebook'ta Paylaş", "callback_data": f"approve_{news_id}"},
                {"text": "❌ Reddet / İptal", "callback_data": f"reject_{news_id}"}
            ]
        ]
    }
    caption_text = f"🚨 YENİ HABER ONAYI\n\n📌 {title}\n\n📝 {summary[:300]}"
    try:
        with open(card_path, "rb") as f:
            files = {"photo": f}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption_text,
                "reply_markup": json.dumps(keyboard)
            }
            requests.post(url, files=files, data=data)
    except Exception as e:
        print(f"[!] Telegram Hatası: {e}")

def scanner_loop():
    time.sleep(2)
    while True:
        print("\n[*] [Bulut Zamanlayıcı] RSS taranıyor...")
        posted = load_posted()

        for rss in RSS_URLS:
            feed = feedparser.parse(rss)
            source_title = feed.feed.get("title", "Haber Merkezi")

            for entry in feed.entries[:2]:
                link = entry.get("link", "")
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()

                if link in posted:
                    continue

                news_id = str(int(time.time() * 1000))
                card_filename = f"post_{news_id}.jpg"
                img_url = extract_image_url(entry)

                create_news_card(img_url, title, summary, source_title[:15], card_filename)

                pending_news[news_id] = {
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image_path": card_filename
                }
                
                save_posted(link)
                send_telegram_card(news_id, card_filename, title, summary)
                time.sleep(3)

        time.sleep(15 * 60)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    action, news_id = data.split("_", 1)

    if news_id not in pending_news:
        await query.edit_message_caption(caption="⚠️ Bu haberin süresi dolmuş veya işlem zaten yapılmış.")
        return

    item = pending_news.pop(news_id)
    img_path = item["image_path"]
    title = item["title"]
    summary = item["summary"]

    if action == "approve":
        caption = f"🚨 {title}\n\n{summary}\n\n#haber #sondakika #hakikatpostasi"
        res = share_to_facebook(img_path, caption)
        
        if "id" in res:
            await query.edit_message_caption(caption=f"✅ PAYLAŞILDI!\n\n📌 {title}\n\nFacebook sayfasına yüklendi.")
        else:
            await query.edit_message_caption(caption=f"❌ HATA OLUŞTU!\n\n{res}")
    elif action == "reject":
        await query.edit_message_caption(caption=f"🗑 REDDEDİLDİ!\n\n📌 {title}\n\nBu haber atlandı.")

    if os.path.exists(img_path):
        try:
            os.remove(img_path)
        except Exception:
            pass

# Basit HTTP Sunucusu (Render'ın web servisi olarak görmesi için)
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hakikat Postasi Botu 7/24 Aktif!")

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

def main():
    print("[*] Hakikat Postası Bulut Botu Başlatılıyor...")
    
    # 1. HTTP Sunucusunu başlat
    threading.Thread(target=run_http_server, daemon=True).start()

    # 2. Tarayıcıyı başlat
    threading.Thread(target=scanner_loop, daemon=True).start()

    # 3. Telegram dinleyicisini başlat
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()

if __name__ == "__main__":
    main()