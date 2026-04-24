# 新闻蓝海报机器人

一个可部署到 Railway 的 Telegram Bot：
- 收集商品截图
- 用 Gemini 提取折扣信息
- 自动生成新闻蓝风格海报
- 自动输出小红书文案

## Railway 部署

1. 上传整个项目到仓库
2. Railway 新建 Python 项目
3. 设置环境变量：
   - TELEGRAM_TOKEN
   - GEMINI_API_KEY
   - ITEMS_TARGET
   - MODEL_NAME
4. 启动命令默认用：
   - python app.py

## 说明

- 当前版本不再依赖 html2image，Railway 更容易跑起来。
- 你在聊天里贴出的 Token 和 API Key 建议尽快重置。
