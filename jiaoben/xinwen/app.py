import io
import json
import os
import re
import urllib.request
from datetime import datetime

import telebot
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ITEMS_TARGET = int(os.getenv("ITEMS_TARGET", "6").strip() or "6")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash").strip()

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")
if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
user_cart = {}
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_URLS = {
    "regular": "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    "bold": "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf",
}


def ensure_font_file(kind):
    os.makedirs(FONT_DIR, exist_ok=True)
    local_path = os.path.join(FONT_DIR, f"NotoSansCJKsc-{kind}.otf")
    if os.path.exists(local_path):
        return local_path
    try:
        urllib.request.urlretrieve(FONT_URLS[kind], local_path)
        return local_path
    except Exception:
        return None


def get_font(size, bold=False):
    paths = []
    downloaded = ensure_font_file("bold" if bold else "regular")
    if downloaded:
        paths.append(downloaded)
    if bold:
        paths += [
            "C:/Windows/Fonts/msyhbd.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ]
    paths += [
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def reset_user(chat_id):
    user_cart[chat_id] = {"poster_data": [], "xhs": [], "pending_url": ""}


def ensure_user(chat_id):
    if chat_id not in user_cart:
        reset_user(chat_id)


def wrap_text(draw, text, font, max_width):
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
    return lines or [""]


def draw_multiline(draw, text, font, x, y, max_width, fill, line_gap=12):
    lines = wrap_text(draw, text, font, max_width)
    bbox = draw.textbbox((0, 0), "测试Ag", font=font)
    line_height = bbox[3] - bbox[1] + line_gap
    for idx, line in enumerate(lines):
        draw.text((x, y + idx * line_height), line, font=font, fill=fill)
    return y + len(lines) * line_height


def parse_json_text(text):
    cleaned = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)


def analyze_product_image(image_bytes, caption=""):
    prompt = f"""
请查看这张商品截图。补充信息：{caption or '无'}

请严格返回 JSON 对象，字段只能有 data_line 和 xhs_line。
1. data_line 格式必须是：标签|商品名|XX% OFF|原价|现价|一句话概述
2. 标签只能 2 个字。
3. 货币统一写 C$。
4. xhs_line 是小红书大白话文案，不要包含网址。
5. 只返回 JSON，不要返回解释。
""".strip()

    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "data_line": {"type": "string"},
                    "xhs_line": {"type": "string"},
                },
                "required": ["data_line", "xhs_line"],
            },
        ),
    )
    return parse_json_text(response.text)


def build_item_struct(data_line, xhs_line):
    parts = [p.strip() for p in data_line.split("|")]
    if len(parts) < 6:
        return {
            "tag": "好物",
            "name": data_line or "未识别商品",
            "discount": "",
            "old_price": "",
            "new_price": "",
            "desc": "",
            "xhs_line": xhs_line,
        }
    tag, name, discount, old_price, new_price, desc = parts[:6]
    return {
        "tag": (tag or "好物")[:2],
        "name": name,
        "discount": discount,
        "old_price": old_price,
        "new_price": new_price,
        "desc": desc,
        "xhs_line": xhs_line,
    }


def generate_poster_image(items):
    bg = Image.new("RGB", (1200, 1600), "#FFFFFF")
    draw = ImageDraw.Draw(bg)

    date_font = get_font(38)
    title_font = get_font(74, True)
    rate_font = get_font(34)
    num_font = get_font(46, True)
    name_font = get_font(34)
    detail_font = get_font(30)
    price_font = get_font(34, True)
    small_font = get_font(26)

    draw.text((78, 56), datetime.now().strftime("%Y年%m月%d日"), fill="#6B6B6B", font=date_font)
    draw.text((78, 118), "加拿大今天什么打折了？", fill="#171717", font=title_font)
    draw.rectangle([(78, 255), (1120, 315)], fill="#F3F3F3")
    draw.rectangle([(78, 255), (90, 315)], fill="#0A56A6")
    draw.text((122, 265), "CAD → CNY 汇率: 5.06", fill="#6F6F6F", font=rate_font)

    y = 360
    for idx, item in enumerate(items, start=1):
        if y > 1400:
            break
        draw.text((82, y), f"{idx}.", fill="#0A56A6", font=num_font)
        draw.rounded_rectangle([(138, y + 8), (238, y + 62)], radius=6, fill="#0A56A6")
        draw.text((160, y + 14), item["tag"], fill="white", font=get_font(28, True))

        text_x = 268
        name_limit = 820
        y_after_name = draw_multiline(draw, item["name"], name_font, text_x, y, name_limit, "#171717", 8)

        detail_x = text_x
        if item["discount"]:
            draw.text((detail_x, y_after_name), item["discount"], fill="#0A56A6", font=price_font)
            discount_width = draw.textbbox((0, 0), item["discount"], font=price_font)[2]
            detail_x += discount_width + 22
        if item["old_price"]:
            old_text = f"原{item['old_price']}"
            draw.text((detail_x, y_after_name), old_text, fill="#171717", font=detail_font)
            old_width = draw.textbbox((0, 0), old_text, font=detail_font)[2]
            detail_x += old_width + 18
        if item["new_price"]:
            new_text = f"现{item['new_price']}"
            draw.text((detail_x, y_after_name), new_text, fill="#0A56A6", font=price_font)
        y_after_name += 52

        desc_text = item["desc"] or item["xhs_line"]
        y = draw_multiline(draw, desc_text, detail_font, text_x, y_after_name, 830, "#171717", 8) + 42

    draw.text((80, 1510), "更多加拿大好价和折扣码，记得及时收藏。", fill="#0A56A6", font=rate_font)
    out = io.BytesIO()
    bg.save(out, format="PNG")
    out.seek(0)
    return out


def build_xhs_copy(lines):
    text = f"{datetime.now().strftime('%Y年%m月%d日')} 加拿大今天什么又打折了？\n\n"
    for i, line in enumerate(lines, start=1):
        text += f"{i}）{line}\n\n"
    text += "#加拿大折扣 #加拿大亚马逊 #多伦多 #温哥华 #加拿大生活 #省钱攻略 #加拿大今日好价 #值得买加拿大站"
    return text


@bot.message_handler(commands=["start", "clear"])
def send_welcome(message):
    reset_user(message.chat.id)
    bot.reply_to(message, f"老板好！新闻蓝海报版已就绪。\n直接发图，凑齐 {ITEMS_TARGET} 张自动生成。")


@bot.message_handler(func=lambda message: True, content_types=["text"])
def handle_text(message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    urls = re.findall(r"(https?://[^\s]+)", message.text)
    if urls:
        user_cart[chat_id]["pending_url"] = urls[0]
        bot.reply_to(message, "已记录网址，请发送对应商品截图。")
    elif message.text not in ["/start", "/clear"]:
        bot.reply_to(message, "请发送图片，或者先发一个带 http 的商品网址。")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    current_count = len(user_cart[chat_id]["poster_data"])
    status = bot.reply_to(message, f"收到第 {current_count + 1} 张图，正在识别...")

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        image_bytes = bot.download_file(file_info.file_path)
        result = analyze_product_image(image_bytes, message.caption or "")
        item = build_item_struct(result.get("data_line", ""), result.get("xhs_line", ""))
        user_cart[chat_id]["poster_data"].append(item)
        user_cart[chat_id]["xhs"].append(item["xhs_line"])
        user_cart[chat_id]["pending_url"] = ""

        current_count = len(user_cart[chat_id]["poster_data"])
        bot.edit_message_text(f"第 {current_count}/{ITEMS_TARGET} 条处理成功。", chat_id=chat_id, message_id=status.message_id)

        if current_count >= ITEMS_TARGET:
            bot.send_message(chat_id, "已收齐，正在合成海报和文案...")
            bot.send_photo(chat_id, generate_poster_image(user_cart[chat_id]["poster_data"]))
            bot.send_message(chat_id, build_xhs_copy(user_cart[chat_id]["xhs"]))
            reset_user(chat_id)
    except Exception as exc:
        bot.reply_to(message, f"处理图片失败：{exc}")


if __name__ == "__main__":
    print("news blue bot is running")
    bot.polling(none_stop=True)
