import datetime
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

import google.generativeai as genai
import requests
import telebot
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps
from telebot.types import KeyboardButton, ReplyKeyboardMarkup

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
RAINFOREST_KEY = os.getenv("RAINFOREST_KEY", "")
AMZ_TAG = os.getenv("AMZ_TAG", "zhidemai0a-20")
SHOPIFY_DOMAIN = os.getenv("SHOPIFY_DOMAIN", "")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
BLOG_ID = os.getenv("BLOG_ID", "")
BOT_FONT_PATH = os.getenv("BOT_FONT_PATH", "")

VALID_DOMAINS = ["myshopify.com", "worthbuy.ca", "zhidemai.ca"]

BTN_COVER = "1 生成今日封面"
BTN_COPY = "2 生成合集文案"
BTN_SYNC = "3 同步合集到博客"
BTN_CLEAR = "0 清空今日列表"
BTN_HELP = "怎么一键盘点选品？"

FONT_CACHE_PATH = Path(tempfile.gettempdir()) / "telegram_railway_bot_noto_sc.otf"
FONT_URLS = [
    "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf",
    "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
]

for env_name in [
    "TELEGRAM_TOKEN",
    "GEMINI_KEY",
    "RAINFOREST_KEY",
    "SHOPIFY_DOMAIN",
    "SHOPIFY_ACCESS_TOKEN",
    "BLOG_ID",
]:
    if not os.getenv(env_name):
        raise ValueError(f"Missing {env_name}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

daily_items = {}
current_code = {}
album_cache = {}


def ensure_font_file() -> Optional[Path]:
    if BOT_FONT_PATH and Path(BOT_FONT_PATH).exists():
        return Path(BOT_FONT_PATH)
    if FONT_CACHE_PATH.exists():
        return FONT_CACHE_PATH

    for url in FONT_URLS:
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            FONT_CACHE_PATH.write_bytes(response.content)
            return FONT_CACHE_PATH
        except Exception:
            continue
    return None


def get_font(size: int):
    candidates = []
    if BOT_FONT_PATH:
        candidates.append(BOT_FONT_PATH)
    candidates.extend(
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/arphic/ukai.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
        ]
    )

    downloaded_font = ensure_font_file()
    if downloaded_font:
        candidates.append(str(downloaded_font))

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton(BTN_COVER), KeyboardButton(BTN_COPY))
    markup.add(KeyboardButton(BTN_SYNC), KeyboardButton(BTN_CLEAR))
    markup.add(KeyboardButton(BTN_HELP))
    return markup


def wrap_text(draw, text: str, font, max_width: int):
    if not text:
        return [""]

    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def draw_centered_text(draw, text: str, font, y: int, fill: str, max_width: int = 900):
    lines = wrap_text(draw, text, font, max_width)
    _, _, _, sample_bottom = draw.textbbox((0, 0), "测试Ag", font=font)
    line_height = sample_bottom + 24
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        width = bbox[2] - bbox[0]
        x = (1080 - width) / 2
        draw.text((x, y + index * line_height), line, fill=fill, font=font)
    return y + len(lines) * line_height


def fetch_image_bytes(url: str) -> Optional[bytes]:
    if not url:
        return None
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.content
    except Exception:
        return None


def search_top_5_amazon(keyword):
    try:
        en_keyword = model.generate_content(
            f"Translate '{keyword}' to an English Amazon search term. Reply with English only."
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
        res = requests.get("https://api.rainforestapi.com/request", params=params, timeout=45)
        res.raise_for_status()
        results = [item for item in res.json().get("search_results", []) if not item.get("sponsored", False)]
        results.sort(key=lambda x: x.get("ratings_total", 0), reverse=True)

        unique_items = []
        seen_asin = set()
        for item in results:
            asin = item.get("asin")
            if asin and asin not in seen_asin:
                seen_asin.add(asin)
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
        res = requests.get(search_url, headers=headers, timeout=30)
        res.raise_for_status()
        products = res.json().get("products", [])
        if not products:
            return None

        product = products[0]
        img_url = product.get("image", {}).get("src", "")
        meta_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-01/products/{product['id']}/metafields.json"
        meta_res = requests.get(meta_url, headers=headers, timeout=30)
        meta_res.raise_for_status()
        metas = meta_res.json().get("metafields", [])

        code = ""
        value = ""
        for meta in metas:
            if meta.get("key") == "coupon_code":
                code = meta.get("value", "")
            elif meta.get("key") == "coupon_value":
                value = meta.get("value", "")

        return {
            "title": product.get("title", handle),
            "code": code or handle,
            "value": value or "Best Deal",
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

    font = get_font(82)
    text = text_content.strip() or "今日折扣"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (1200 - (bbox[2] - bbox[0])) / 2
    y = 700 + (200 - (bbox[3] - bbox[1])) / 2 - 10
    draw.text((x, y), text, fill="white", font=font)

    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=95)
    out.seek(0)
    return out


def make_cover():
    bg = Image.new("RGB", (1080, 1440), "#F4EFFF")
    draw = ImageDraw.Draw(bg)

    date_font = get_font(110)
    quote_font = get_font(250)

    date_text = datetime.datetime.now().strftime("%Y年%m月%d日")
    draw.text((120, 100), "“", fill="#D0C4E8", font=quote_font)
    draw.text((150, 300), date_text, fill="#666666", font=date_font)

    try:
        flag_bytes = fetch_image_bytes("https://flagcdn.com/w160/ca.png")
        if not flag_bytes:
            raise ValueError('missing flag')
        flag = Image.open(io.BytesIO(flag_bytes)).convert("RGBA").resize((140, 70))
        bg.paste(flag, (150, 520), flag)
        draw.text((320, 480), "分享几个", fill="#333333", font=date_font)
    except Exception:
        draw.text((150, 480), "分享几个", fill="#333333", font=date_font)

    draw.rectangle([(470, 780), (950, 810)], fill="#BDE8A5")
    draw.text((150, 680), "加拿大亚马逊的", fill="#333333", font=date_font)
    draw.text((150, 880), "五折CODE!", fill="#333333", font=date_font)

    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=95)
    out.seek(0)
    return out


def post_to_shopify_blog(items, chat_id):
    if not items:
        return False

    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    body_html = f"<h2>加拿大 {date_str} 折扣合集分享</h2>"

    for idx, item in enumerate(items, start=1):
        img_html = ""
        if item.get("image"):
            img_html = (
                f'<div style="margin:20px 0;text-align:center;">'
                f'<img src="{item["image"]}" style="max-width:100%;border-radius:12px;">'
                f"</div>"
            )

        body_html += (
            f'<div style="border-top:1px solid #eee;padding:20px 0;">'
            f'<p style="font-size:20px;"><b>{idx}. {item["title"]}</b></p>'
            f"{img_html}"
            f'<p>直接搜代码：<span style="color:#00A8E1;font-weight:bold;">{item["code"]}</span></p>'
            f'<p>折扣力度：{item["value"]}</p>'
            f"</div>"
        )

    first_item = items[0]
    out_url = (
        f"https://www.amazon.ca/dp/{first_item['code']}?tag={AMZ_TAG}"
        if len(first_item.get("code", "")) == 10
        else "https://zhidemai.ca"
    )
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
            "image": {"src": first_item.get("image", "")},
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

    info = get_shopify_info(target_url)
    if not info:
        return False

    daily_items.setdefault(chat_id, []).append(info)
    current_code[chat_id] = f"CODE: {info['code']}  {info['value']}"

    bot.send_message(
        chat_id,
        f"已成功抓取并加入列表：{info['title'][:18]}...\n当前购物车共有 {len(daily_items[chat_id])} 件商品。",
        reply_markup=get_main_menu(),
    )
    return True


def build_collection_prompt(items, keyword_hint="今日好价"):
    return f"""
请你扮演“值得买加拿大站”的小编，写一篇适合发小红书的盘点文案。

标题方向：加拿大今日折扣 / {keyword_hint}
商品资料：{json.dumps(items, ensure_ascii=False)}

要求：
1. 开头要像朋友分享，热情自然。
2. 每个商品用中文简短介绍，并保留代码或 ASIN。
3. 多用 emoji，但不要使用星号加粗。
4. 最后补一段引导互动的话，并加 4 到 6 个相关标签。
5. 直接输出纯文本，不要输出 Markdown。
""".strip()


@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "控制台已就绪，直接发商品链接、发两张图，或者点菜单开始。",
        reply_markup=get_main_menu(),
    )


@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = message.text.strip()
    chat_id = message.chat.id

    if text in {BTN_COVER, "1"}:
        bot.send_photo(chat_id, make_cover(), reply_markup=get_main_menu())
        return

    if text in {BTN_CLEAR, "0"}:
        daily_items[chat_id] = []
        bot.send_message(chat_id, "今日列表已清空。", reply_markup=get_main_menu())
        return

    if text == BTN_SYNC:
        items = daily_items.get(chat_id, [])
        if not items:
            bot.send_message(chat_id, "列表还是空的，先发商品链接或先盘点选品。")
            return

        bot.send_message(chat_id, "正在同步到 Shopify 博客，请稍等。")
        if post_to_shopify_blog(items, chat_id):
            bot.send_message(chat_id, "同步成功，博客文章已经发布。", reply_markup=get_main_menu())
        else:
            bot.send_message(chat_id, "同步失败，请检查 Shopify 配置或稍后重试。", reply_markup=get_main_menu())
        return

    if text in {BTN_COPY, "2"}:
        items = daily_items.get(chat_id, [])
        if not items:
            bot.send_message(chat_id, "列表还是空的，先发商品链接或先盘点选品。")
            return

        prompt = build_collection_prompt(items)
        try:
            result = model.generate_content(prompt).text
            bot.send_message(chat_id, f"文案已生成：\n\n{result}")
        except Exception as exc:
            bot.send_message(chat_id, f"文案生成失败：{exc}")
        return

    if text == BTN_HELP:
        help_text = (
            "使用方法：\n"
            "1. 回复 1 生成今日封面。\n"
            "2. 发商品链接，机器人会记到今日列表。\n"
            "3. 发两张图片，会自动拼图并叠加当前 code。\n"
            "4. 发送“盘点 空气炸锅”这类指令，可以自动抓亚马逊前 5 个商品并生成文案。\n"
            "5. 回复 3 可同步今日合集到 Shopify 博客。"
        )
        bot.send_message(chat_id, help_text, reply_markup=get_main_menu())
        return

    if text.startswith("盘点 "):
        keyword = text.replace("盘点 ", "", 1).strip()
        msg = bot.send_message(chat_id, f"正在搜索 {keyword} 的热门商品，请稍等。")
        top_items = search_top_5_amazon(keyword)
        if not top_items:
            bot.edit_message_text("抓取失败，暂时没有拿到可用商品。", chat_id, msg.message_id)
            return

        item_data = []
        for item in top_items:
            info = {
                "title": item.get("title", "未知商品"),
                "code": item.get("asin", ""),
                "value": item.get("price", {}).get("raw", "Good Price"),
                "image": item.get("image", ""),
                "rating": item.get("rating", "5.0"),
                "reviews": item.get("ratings_total", 0),
            }
            daily_items.setdefault(chat_id, []).append(info)
            item_data.append(info)

        prompt = build_collection_prompt(item_data, keyword)
        try:
            result_text = model.generate_content(prompt).text
            bot.edit_message_text(
                f"盘点完成，前 5 个商品已加入列表：\n\n{result_text}",
                chat_id,
                msg.message_id,
            )
        except Exception as exc:
            bot.edit_message_text(f"AI 文案生成失败：{exc}", chat_id, msg.message_id)
        return

    if any(domain in text.lower() for domain in VALID_DOMAINS):
        bot.send_message(chat_id, "识别到商品链接，正在抓取信息。")
        if not process_link_to_memory(text, chat_id):
            bot.send_message(chat_id, "链接抓取失败，请确认商品链接可访问。", reply_markup=get_main_menu())
        return

    bot.send_message(chat_id, "我收到啦。你可以回复 1 生成封面，或者直接发商品链接。", reply_markup=get_main_menu())


@bot.message_handler(content_types=["photo"])
def handle_photos(message):
    group_id = message.media_group_id
    chat_id = message.chat.id
    caption_text = message.caption or ""

    if not group_id:
        if any(domain in caption_text.lower() for domain in VALID_DOMAINS):
            bot.send_message(chat_id, "识别到带链接的单张图片，先帮你抓商品信息。")
            process_link_to_memory(caption_text, chat_id)
        else:
            bot.reply_to(message, "请一次发送两张图来拼图，或者在说明文字里附上商品链接。")
        return

    file_info = bot.get_file(message.photo[-1].file_id)
    album_cache.setdefault(group_id, {"photos": [], "text": "", "chat_id": chat_id})
    album_cache[group_id]["photos"].append(bot.download_file(file_info.file_path))

    if message.caption:
        album_cache[group_id]["text"] = message.caption

    if len(album_cache[group_id]["photos"]) < 2:
        return

    caption = album_cache[group_id]["text"]
    if caption and any(domain in caption.lower() for domain in VALID_DOMAINS):
        bot.send_message(chat_id, "检测到链接，先同步商品信息。")
        process_link_to_memory(caption, chat_id)

    code_text = current_code.get(chat_id, "今日折扣")
    bot.send_message(chat_id, "正在生成拼图，请稍等。")
    try:
        collage = make_collage(
            album_cache[group_id]["photos"][0],
            album_cache[group_id]["photos"][1],
            code_text,
        )
        bot.send_photo(chat_id, collage, reply_markup=get_main_menu())
    except Exception as exc:
        bot.send_message(chat_id, f"拼图失败：{exc}", reply_markup=get_main_menu())
    finally:
        album_cache.pop(group_id, None)


print("Bot is running.")
bot.polling(none_stop=True)



