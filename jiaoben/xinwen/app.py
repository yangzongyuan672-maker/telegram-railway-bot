import io
import json
import os
import re
from datetime import datetime

import telebot
from dotenv import load_dotenv
from google import genai
from google.genai import types
from html2image import Html2Image  # 换回了强大的网页排版工具

load_dotenv()

# 安全读取环境变量，不会暴露你的 API
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

# 初始化网页截图工具，尺寸定为 1200x1600
hti = Html2Image()
hti.size = (1200, 1600)


def reset_user(chat_id):
    user_cart[chat_id] = {"poster_data": [], "xhs": [], "pending_url": ""}


def ensure_user(chat_id):
    if chat_id not in user_cart:
        reset_user(chat_id)


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
            "html": data_line or "未识别商品",
            "xhs_line": xhs_line,
        }
    tag, name, discount, old_price, new_price, desc = parts[:6]
    
    # 核心：在这里直接拼装成带蓝色高亮的 HTML 格式
    html_format = f"{name} <span class='highlight-blue'>{discount}</span> 原{old_price} 现<span class='highlight-blue'>{new_price}</span>。{desc}"
    
    return {
        "tag": (tag or "好物")[:2],
        "html": html_format,
        "xhs_line": xhs_line,
    }


def generate_poster_image(chat_id, items):
    today_str = datetime.now().strftime("%Y年%m月%d日")
    
    # 你最爱的绝美排版 CSS
    items_html = """
    <style>
        .zdm-item { margin-bottom: 32px !important; display: flex; align-items: flex-start; }
        .zdm-item-num { font-size: 1.35em; font-weight: bold; color: #0A56A6; margin-right: 12px; line-height: 1.4; padding-top: 1px; }
        .zdm-item-text-area { 
            flex: 1; font-size: 1.25em; line-height: 1.45; color: #222; 
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis;
        }
        .zdm-item-tag { background: #0A56A6; color: white; padding: 4px 10px; border-radius: 4px; font-size: 0.85em; margin-right: 8px; vertical-align: text-bottom; display: inline-block; margin-bottom: 2px;}
        .highlight-blue { font-weight: bold; color: #0A56A6; } 
    </style>
    """
    
    for index, item in enumerate(items):
        items_html += f"""
        <div class="zdm-item">
          <span class="zdm-item-num">{index + 1}.</span>
          <div class="zdm-item-text-area">
            <span class="zdm-item-tag">{item['tag']}</span>
            <span class="zdm-item-text">{item['html']}</span>
          </div>
        </div>
        """

    temp_html_name = f"temp_{chat_id}.html"
    image_name = f"poster_{chat_id}.png"

    try:
        # 读取你上传的 HTML 模板文件
        with open("poster_template.html", "r", encoding="utf-8") as f:
            template = f.read()
        final_html = template.replace("{{DATE}}", today_str).replace("{{ITEMS_HTML}}", items_html)
        
        with open(temp_html_name, "w", encoding="utf-8") as f:
            f.write(final_html)

        # 生成截图
        hti.screenshot(html_file=temp_html_name, save_as=image_name)

        # 读取图片变成内存数据发给 Telegram
        with open(image_name, 'rb') as photo:
            img_bytes = photo.read()
            
    finally:
        # 打扫战场，删掉临时文件
        if os.path.exists(temp_html_name):
            os.remove(temp_html_name)
        if os.path.exists(image_name):
            os.remove(image_name)
            
    out = io.BytesIO(img_bytes)
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
            
            # 使用 Html2Image 生成海报
            poster_io = generate_poster_image(chat_id, user_cart[chat_id]["poster_data"])
            bot.send_photo(chat_id, poster_io)
            bot.send_message(chat_id, build_xhs_copy(user_cart[chat_id]["xhs"]))
            reset_user(chat_id)
            
    except Exception as exc:
        bot.reply_to(message, f"处理图片失败：{exc}")


if __name__ == "__main__":
    print("news blue bot is running (Html2Image Edition)")
    bot.polling(none_stop=True)
