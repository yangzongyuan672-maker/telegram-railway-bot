import os
import re
import json
import datetime
import io

import telebot
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
import google.generativeai as genai
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
RAINFOREST_KEY = os.getenv("RAINFOREST_KEY", "")
AMZ_TAG = os.getenv("AMZ_TAG", "zhidemai0a-20")
SHOPIFY_DOMAIN = os.getenv("SHOPIFY_DOMAIN", "")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
BLOG_ID = os.getenv("BLOG_ID", "")

VALID_DOMAINS = ["myshopify.com", "worthbuy.ca", "zhidemai.ca"]

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")
if not GEMINI_KEY:
    raise ValueError("Missing GEMINI_KEY")
if not RAINFOREST_KEY:
    raise ValueError("Missing RAINFOREST_KEY")
if not SHOPIFY_DOMAIN:
    raise ValueError("Missing SHOPIFY_DOMAIN")
if not SHOPIFY_ACCESS_TOKEN:
    raise ValueError("Missing SHOPIFY_ACCESS_TOKEN")
if not BLOG_ID:
    raise ValueError("Missing BLOG_ID")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

daily_items = {}
current_code = {}
album_cache = {}


def get_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "msyhbd.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🖼️ 生成今日封面"), KeyboardButton("📝 生成合集文案"))
    markup.add(KeyboardButton("📰 同步合集到博客"), KeyboardButton("🗑️ 清空今日列表"))
    markup.add(KeyboardButton("💡 怎么一键盘点选品？"))
    return markup


def search_top_5_amazon(keyword):
    try:
        en_keyword = model.generate_content(
            f"Translate '{keyword}' to English Amazon search term, reply only English."
        ).text.strip()
    except Exception:
        en_keyword = keyword

    params = {
        "api_key": RAINFOREST_KEY,
        "type": "search",
        "amazon_domain": "amazon.ca",
        "search_term": en_keyword,
    }

    try:
        res = requests.get(
            "https://api.rainforestapi.com/request",
            params=params,
            timeout=45,
        ).json()
        results = [item for item in res.get("search_results", []) if not item.get("sponsored", False)]
        results.sort(key=lambda x: x.get("ratings_total", 0), reverse=True)

        unique_items = []
        seen_reviews = set()
        for item in results:
            rev = item.get("ratings_total", 0)
            if rev > 0 and rev not in seen_reviews:
                seen_reviews.add(rev)
                unique_items.append(item)
            if len(unique_items) == 5:
                break
        return unique_items
    except Exception:
        return []


def get_shopify_info(product_url):
    try:
        handle = product_url.strip().split("/")[-1].split("?")[0]
        search_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-01/products.json?handle={handle}"
        headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}
        res = requests.get(search_url, headers=headers, timeout=30).json()
        if not res.get("products"):
            return None

        p = res["products"][0]
        img_url = p.get("image", {}).get("src", "")
        meta_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-01/products/{p['id']}/metafields.json"
        metas = requests.get(meta_url, headers=headers, timeout=30).json().get("metafields", [])

        code = ""
        value = ""
        for m in metas:
            if m["key"] == "coupon_code":
                code = m["value"]
            if m["key"] == "coupon_value":
                value = m["value"]

        return {
            "title": p["title"],
            "code": code if code else handle,
            "value": value if value else "Best Deal",
            "image": img_url,
            "rating": "5.0",
            "reviews": 200,
        }
    except Exception:
        return None


def make_collage(img1_bytes, img2_bytes, text_content):
    img1 = ImageOps.fit(
        Image.open(io.BytesIO(img1_bytes)).convert("RGB"),
        (1200, 700),
        Image.Resampling.LANCZOS,
    )
    img2 = ImageOps.fit(
        Image.open(io.BytesIO(img2_bytes)).convert("RGB"),
        (1200, 700),
        Image.Resampling.LANCZOS,
    )

    bg = Image.new("RGB", (1200, 1600), "#FFFFFF")
    bg.paste(img1, (0, 0))
    bg.paste(img2, (0, 900))

    draw = ImageDraw.Draw(bg)
    draw.rounded_rectangle([(450, 480), (1050, 680)], outline="#E1306C", width=12, radius=25)
    draw.rectangle([(0, 700), (1200, 900)], fill="#D32F2F")

    font = get_font(85)
    bbox = draw.textbbox((0, 0), text_content, font=font)
    draw.text(
        ((1200 - (bbox[2] - bbox[0])) / 2, 700 + (200 - (bbox[3] - bbox[1])) / 2 - 10),
        text_content,
        fill="white",
        font=font,
    )

    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=95)
    out.seek(0)
    return out


def make_cover():
    bg = Image.new("RGB", (1080, 1440), "#F4EFFF")
    draw = ImageDraw.Draw(bg)

    f_t = get_font(110)
    f_q = get_font(250)

    date_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    draw.text((120, 100), "“", fill="#D0C4E8", font=f_q)
    draw.text((150, 300), date_str, fill="#666666", font=f_t)

    try:
        flag = Image.open(io.BytesIO(requests.get("https://flagcdn.com/w160/ca.png", timeout=30).content)).convert("RGBA").resize((140, 70))
        bg.paste(flag, (150, 520), flag)
        draw.text((320, 480), "分享几个", fill="#333333", font=f_t)
    except Exception:
        draw.text((150, 480), "分享几个", fill="#333333", font=f_t)

    draw.rectangle([(470, 780), (950, 810)], fill="#BDE8A5")
    draw.text((150, 680), "加拿大亚马逊的", fill="#333333", font=f_t)
    draw.text((150, 880), "五折CODE!", fill="#333333", font=f_t)

    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=95)
    out.seek(0)
    return out


def post_to_shopify_blog(items, chat_id):
    if not items:
        return False

    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    body_html = f"<h2>加拿大 {date_str} 爆款折扣合集分享</h2>"

    for idx, item in enumerate(items):
        img_html = (
            f'<div style="margin: 20px 0; text-align: center;"><img src="{item["image"]}" style="max-width: 100%; border-radius: 12px;"></div>'
            if item.get("image")
            else ""
        )
        body_html += (
            f'<div style="border-top: 1px solid #eee; padding: 20px 0;">'
            f'<p style="font-size: 20px;"><b>{idx+1}. {item["title"]}</b></p>'
            f"{img_html}"
            f'<p>直接搜代码：<span style="color: #00A8E1; font-weight: bold;">{item["code"]}</span></p>'
            f"<p>折扣力度：{item['value']}</p>"
            f"</div>"
        )

    first_item = items[0]
    out_url = f"https://www.amazon.ca/dp/{first_item['code']}?tag={AMZ_TAG}" if len(first_item["code"]) == 10 else "https://zhidemai.ca"
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-01/blogs/{BLOG_ID}/articles.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }

    payload = {
        "article": {
            "title": f"加拿大 {date_str} 限时折扣合集",
            "author": "Amy",
            "body_html": body_html,
            "published": True,
            "template_suffix": "top10",
            "image": {"src": first_item["image"]},
            "metafields": [
                {"namespace": "custom", "key": "url", "value": out_url, "type": "url"},
                {
                    "namespace": "custom",
                    "key": "pingfen",
                    "value": json.dumps({"value": "5.0", "scale_min": "1.0", "scale_max": "5.0"}),
                    "type": "rating",
                },
                {"namespace": "custom", "key": "pingjiashuliang", "value": "200", "type": "number_integer"},
            ],
        }
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        return res.status_code in [200, 201]
    except Exception:
        return False


def process_link_to_memory(text_content, chat_id):
    urls = re.findall(r"(https?://[^\s]+)", text_content)
    target_url = urls[0] if urls else text_content
    if not any(domain in target_url.lower() for domain in VALID_DOMAINS):
        return False

    try:
        handle = target_url.strip().split("/")[-1].split("?")[0]
        search_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-01/products.json?handle={handle}"
        headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}
        res = requests.get(search_url, headers=headers, timeout=30).json()
        if not res.get("products"):
            return False

        p = res["products"][0]
        img_url = p.get("image", {}).get("src", "")
        meta_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-01/products/{p['id']}/metafields.json"
        metas = requests.get(meta_url, headers=headers, timeout=30).json().get("metafields", [])

        code = ""
        value = ""
        for m in metas:
            if m["key"] == "coupon_code":
                code = m["value"]
            if m["key"] == "coupon_value":
                value = m["value"]

        info = {
            "title": p["title"],
            "code": code if code else handle,
            "value": value if value else "Best Deal",
            "image": img_url,
            "rating": "5.0",
            "reviews": 200,
        }

        if chat_id not in daily_items:
            daily_items[chat_id] = []
        daily_items[chat_id].append(info)
        current_code[chat_id] = f"CODE: {info['code']}  {info['value']}"

        bot.send_message(
            chat_id,
            f"✅ 已成功抓取并加入列表：{info['title'][:15]}...\n📊 当前购物车共有 {len(daily_items[chat_id])} 件商品。",
            reply_markup=get_main_menu(),
        )
        return True
    except Exception:
        return False


@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "🤖 控制台已就绪！", reply_markup=get_main_menu())


@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = message.text.strip()
    chat_id = message.chat.id

    if text == "🖼️ 生成今日封面" or text == "1":
        bot.send_photo(chat_id, make_cover(), reply_markup=get_main_menu())

    elif text == "🗑️ 清空今日列表" or text == "0":
        daily_items[chat_id] = []
        bot.send_message(chat_id, "🗑️ 记忆已清空！", reply_markup=get_main_menu())

    elif text == "📰 同步合集到博客":
        items = daily_items.get(chat_id, [])
        if not items:
            bot.send_message(chat_id, "📭 列表为空。")
            return

        bot.send_message(chat_id, "🌐 正在同步...")
        if post_to_shopify_blog(items, chat_id):
            bot.send_message(chat_id, "✅ 同步成功！")
        else:
            bot.send_message(chat_id, "❌ 同步失败。")

    elif text == "📝 生成合集文案" or text == "2":
        items = daily_items.get(chat_id, [])
        if not items:
            bot.send_message(chat_id, "📭 列表为空。")
            return

        prompt = f"写一篇小红书盘点文案。标题：加拿大今日折扣。商品列表：{str(items)}。禁止用星号加粗！多用Emoji。"
        bot.send_message(chat_id, f"📝 文案生成完毕：\n\n{model.generate_content(prompt).text}")

    elif text.startswith("盘点 "):
        keyword = text.replace("盘点 ", "").strip()
        msg = bot.send_message(chat_id, f"📡 正在搜罗的真实爆款...")

        top_items = search_top_5_amazon(keyword)
        if not top_items:
            bot.edit_message_text("❌ 抓取失败。", chat_id, msg.message_id)
            return

        item_data_str = ""
        for idx, item in enumerate(top_items):
            info = {
                "title": item.get("title", "未知商品"),
                "code": item.get("asin", ""),
                "value": item.get("price", {}).get("raw", "Good Price"),
                "image": item.get("image", ""),
                "rating": item.get("rating", "5.0"),
                "reviews": item.get("ratings_total", 0),
            }
            if chat_id not in daily_items:
                daily_items[chat_id] = []
            daily_items[chat_id].append(info)

            item_data_str += (
                f"第{idx+1}名: {info['title']}\n"
                f"ASIN: {info['code']}\n"
                f"价格: {info['value']}\n"
                f"评分: {info['rating']}\n"
                f"评价数: {info['reviews']}\n\n"
            )

        prompt = f"""
请你扮演“值得买加拿大站”的小编，帮我写一篇小红书盘点笔记。
大标题：2026年加拿大最值得入手的5款{keyword}推荐

资料如下：
{item_data_str}

要求严格按照以下格式和排版风格输出（不要自由发挥排版）：
1. 开头：用极其热情、闺蜜分享的口吻打招呼（如：姐妹们！夏天就要到啦！...），引出今天推荐的5款{keyword}。
2. 商品列表格式：
   1. 极简中文商品名 (直接搜代码：ASIN)
      ✨ [一句话卖点和推荐理由，闺蜜口吻，突出痛点]
      💰 价格：[对应价格]
      🌟 评分：高达[对应评分]分，有[对应评价数]个真实评价，[附带一句感叹，如“群众的眼睛是雪亮的”]
   (以此类推写完5个)
3. 结尾：热情互动，号召大家去搜代码购买。
4. 标签：结尾加上相关的话题标签，如 #{keyword} #加拿大生活 #亚马逊好物 等。
5. ：绝对禁止在任何地方使用星号（*）进行加粗或排版！必须输出干净的纯文本！
"""
        try:
            result_text = model.generate_content(prompt).text
            bot.edit_message_text(
                f"🏆 盘点完成！前5名已加入列表：\n\n{result_text}",
                chat_id,
                msg.message_id,
            )
        except Exception as e:
            bot.edit_message_text(f"❌ AI 生成文案失败: {e}", chat_id, msg.message_id)

    elif any(domain in text.lower() for domain in VALID_DOMAINS):
        bot.send_message(chat_id, "🔍 识别到链接，正在抓取...")
        process_link_to_memory(text, chat_id)


@bot.message_handler(content_types=["photo"])
def handle_photos(message):
    group_id = message.media_group_id
    chat_id = message.chat.id
    caption_text = message.caption if message.caption else ""

    if not group_id:
        if any(domain in caption_text.lower() for domain in VALID_DOMAINS):
            bot.send_message(chat_id, "🔍 识别到单张带链接图，正在抓取...")
            process_link_to_memory(caption_text, chat_id)
        else:
            bot.reply_to(message, "请发两张图拼图，或确保文案带链接！")
        return

    file_info = bot.get_file(message.photo[-1].file_id)
    if group_id not in album_cache:
        album_cache[group_id] = {"photos": [], "text": ""}

    album_cache[group_id]["photos"].append(bot.download_file(file_info.file_path))
    if message.caption:
        album_cache[group_id]["text"] = message.caption

    if len(album_cache[group_id]["photos"]) == 2:
        cap_txt = album_cache[group_id]["text"]
        if cap_txt and any(domain in cap_txt.lower() for domain in VALID_DOMAINS):
            bot.send_message(chat_id, "🔍 同步链接中...")
            process_link_to_memory(cap_txt, chat_id)

        code_text = current_code.get(chat_id, "今日抢购")
        bot.send_message(chat_id, "🖼️ 拼图生成中...")
        try:
            bot.send_photo(
                chat_id,
                make_collage(
                    album_cache[group_id]["photos"][0],
                    album_cache[group_id]["photos"][1],
                    code_text,
                ),
                reply_markup=get_main_menu(),
            )
        except Exception as e:
            bot.send_message(chat_id, f"❌ 拼图失败: {e}")

        del album_cache[group_id]


print("🚀 完美排版机器人已启动！")
bot.polling(none_stop=True)
