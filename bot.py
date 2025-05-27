import os
import logging
import hashlib
import hmac
from flask import Flask, request, abort
import requests

from dotenv import load_dotenv
load_dotenv()

# Настройки
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}" if os.getenv("RENDER_EXTERNAL_HOSTNAME") else None

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Проверка подписи (опционально, для безопасности)
def verify_signature(req):
    return True  # Можно добавить проверку HMAC здесь, если нужно

# Установка вебхука
def set_webhook():
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL not set. Make sure RENDER_EXTERNAL_HOSTNAME is available.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    response = requests.post(url, json={"url": WEBHOOK_URL})
    if response.ok:
        logger.info(f"Webhook set successfully: {WEBHOOK_URL}")
    else:
        logger.error(f"Failed to set webhook: {response.text}")

# Обработка запроса от Telegram
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if not verify_signature(request):
        abort(403)

    update = request.get_json()
    logger.info(f"Received update: {update}")

    # Обработка команды /start
    if "message" in update and update["message"].get("text") == "/start":
        chat_id = update["message"]["chat"]["id"]
        send_message(chat_id, "Привет! Я бот.")

    return "ok"

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    response = requests.post(url, json=payload)
    if not response.ok:
        logger.error(f"Failed to send message: {response.text}")

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=PORT)
