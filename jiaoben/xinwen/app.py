import datetime
import io
import json
import os
import re
import time

import telebot
from html2image import Html2Image
import google.generativeai as genai
from PIL import Image

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ITEMS_TARGET = int(os.getenv("ITEMS_TARGET", "6").strip() or "6")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash").strip()

if not BOT_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")
if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(
    MODEL_NAME,
    generation_config={"response_mime_type": "application/json"},
)

hti = Html2Image()
hti.size = (1200, 1600)

user_cart = {}


def reset_user(chat_id):
    user_cart[chat_id] = {"poster_data": [], "xhs": [], "pending_url": ""}


def ensure_user(chat_id):
    if chat_id not in user_cart:
        reset_user(chat_id)


def generate_with_retry(prompt, img, retries=4):
    last_error = None
    for i in range(retries):
        try:
            return model.generate_content([prompt, img])
        except Exception as e:
            last_error = e
            msg = str(e)
            if "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg:
                if i < retries - 1:
                    time.sleep(3 * (i + 1))
                    continue
            raise
    raise last_error


print("[新闻蓝海报机器人] 已启动")


@bot.message_handler(commands=["start", "clear"])
def send_welcome(message):
    reset_user(message.chat.id)
    bot.reply_to(
        message,
        f"老板好！【新闻蓝排版 + 小红书大白话文案】已就绪。\n直接发图，凑齐{ITEMS_TARGET}张自动生成！",
    )


@bot.message_handler(func=lambda message: True, content_types=["text"])
def handle_text(message):
    chat_id = message.chat.id
    ensure_user(chat_id)

    urls = re.findall(r"(https?://[^\s]+)", message.text)
    if urls:
        user_cart[chat_id]["pending_url"] = urls[0]
        bot.reply_to(message, "已记录网址，请发送对应的商品截图。")
    else:
        if message.text not in ["/start", "/clear"]:
            bot.reply_to(message, "请发送图片或者包含 http 的网址链接。")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    chat_id = message.chat.id
    ensure_user(chat_id)

    user_caption = message.caption if message.caption else ""
    caption_urls = re.findall(r"(https?://[^\s]+)", user_caption)
    if caption_urls:
        item_url = caption_urls[0]
    else:
        item_url = user_cart[chat_id]["pending_url"]

    current_count = len(user_cart[chat_id]["poster_data"])
    msg = bot.reply_to(
        message,
        f"收到第 {current_count + 1} 张图，正在生成新闻风数据和大白话文案...",
    )

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        img = Image.open(io.BytesIO(downloaded_file))

        extra_info = f"\n【补充信息】：{user_caption}\n" if user_caption else ""

        prompt = f"""
请查看商品截图。{extra_info}

请务必输出合法的 JSON 格式数据，包含两个字段 "data_line" 和 "xhs_line"：

1. "data_line" 的要求：
用竖线 "|" 分隔。标签必须只能2个字；货币统一用 C$；【商品名】+【一句话概述】总和不超过35个中文字符。
格式：标签|商品名|XX% OFF|原价|现价|一句话概述
示例："服饰|lululemon 运动夹克|60% OFF|C$148|C$59|黑白百搭断码速抢"

2. "xhs_line" 的要求：
小红书大白话文案（不要包含网址）。用大白话讲一下这个商品的折扣活动，并紧跟两句话的种草推荐语。
示例："lululemon夹克现在直接打6折，原价C$148现在只要C$59！这款黑白配色超级百搭，材质轻薄透气。官网现在已经开始断码了，拼手速的时候到了。"
"""

        response = generate_with_retry(prompt, img)

        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        result_dict = json.loads(clean_text)

        data_line = result_dict.get("data_line", "")
        xhs_line = result_dict.get("xhs_line", "解析文案失败")

        parts = [p.strip() for p in data_line.split("|")]

        if len(parts) >= 6:
            tag, name, discount, old_p, new_p, desc = parts[:6]
            html_format = (
                f"{name} <span class='highlight-blue'>{discount}</span> "
                f"原{old_p} 现<span class='highlight-blue'>{new_p}</span>。{desc}"
            )
            user_cart[chat_id]["poster_data"].append({"tag": tag, "html": html_format})
            user_cart[chat_id]["xhs"].append(xhs_line)
        else:
            user_cart[chat_id]["poster_data"].append({"tag": "好物", "html": data_line})
            user_cart[chat_id]["xhs"].append(xhs_line)

        user_cart[chat_id]["pending_url"] = item_url or ""
        current_count = len(user_cart[chat_id]["poster_data"])
        bot.edit_message_text(
            f"第 {current_count}/{ITEMS_TARGET} 条处理成功！",
            chat_id=chat_id,
            message_id=msg.message_id,
        )

        if current_count >= ITEMS_TARGET:
            bot.send_message(chat_id, "已收齐！正在合成新闻风海报和文案...")
            generate_final_outputs(chat_id, user_cart[chat_id])
            reset_user(chat_id)

    except Exception as e:
        bot.reply_to(message, f"处理图片失败：{str(e)}")


def generate_final_outputs(chat_id, data_dict):
    today_str = datetime.datetime.now().strftime("%Y年%m月%d日")

    items_html = """
    <style>
        .zdm-item { margin-bottom: 32px !important; display: flex; align-items: flex-start; }
        .zdm-item-num { font-size: 1.35em; font-weight: bold; color: #0A56A6; margin-right: 12px; line-height: 1.4; padding-top: 1px; }
        .zdm-item-text-area {
            flex: 1; font-size: 1.25em; line-height: 1.45; color: #222;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis;
        }
        .zdm-item-tag {
            background: #0A56A6; color: white; padding: 4px 10px; border-radius: 4px;
            font-size: 0.85em; margin-right: 8px; vertical-align: text-bottom;
            display: inline-block; margin-bottom: 2px;
        }
        .highlight-blue { font-weight: bold; color: #0A56A6; }
    </style>
    """

    for index, item in enumerate(data_dict["poster_data"]):
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
        with open("poster_template.html", "r", encoding="utf-8") as f:
            template = f.read()

        final_html = template.replace("{{DATE}}", today_str).replace("{{ITEMS_HTML}}", items_html)

        with open(temp_html_name, "w", encoding="utf-8") as f:
            f.write(final_html)

        hti.screenshot(html_file=temp_html_name, save_as=image_name)

        with open(image_name, "rb") as photo:
            bot.send_photo(chat_id, photo)

    except Exception as e:
        bot.send_message(chat_id, f"海报合成失败：\n{str(e)}")
    finally:
        if os.path.exists(temp_html_name):
            os.remove(temp_html_name)
        if os.path.exists(image_name):
            os.remove(image_name)

    xhs_copy = f"{today_str} 加拿大今天什么又打折了？\n\n"
    for i, line in enumerate(data_dict["xhs"]):
        xhs_copy += f"{i + 1}）{line}\n\n"
    xhs_copy += "#加拿大折扣 #加拿大亚马逊 #多伦多 #温哥华 #加拿大生活 #省钱攻略 #加拿大今日好价 #值得买加拿大站"
    bot.send_message(chat_id, xhs_copy)


if __name__ == "__main__":
    bot.polling(none_stop=True)
