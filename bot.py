import os
import logging
import requests
from flask import Flask, request
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

load_dotenv()

# Настройки
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Flask-приложение
app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram Application
application = Application.builder().token(TOKEN).build()

# Состояние пользователей
user_state = {}

# Команда /start
async def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("Вопрос 1", callback_data="category_1")],
        [InlineKeyboardButton("Вопрос 2", callback_data="category_2")],
        [InlineKeyboardButton("Другое", callback_data="category_other")]
    ]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))
    user_state[update.effective_user.id] = {"category": None, "text": "", "files": []}

# Обработка выбора категории
async def category_selected(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    category = query.data.replace("category_", "")
    user_state[query.from_user.id] = {
        "category": category,
        "text": "",
        "files": []
    }

    await query.edit_message_text("Отправьте описание проблемы (можно прикрепить файлы).")

# Обработка текста
async def handle_text(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_state:
        await update.message.reply_text("Пожалуйста, начните с /start")
        return
    user_state[user_id]["text"] += update.message.text + "\n"
    await update.message.reply_text("Принято. Если всё готово, нажмите 'Подтвердить'", reply_markup=confirmation_buttons())

# Обработка файлов
async def handle_document(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_state:
        await update.message.reply_text("Пожалуйста, начните с /start")
        return
    file = update.message.document or update.message.photo[-1]
    file_id = file.file_id
    file_unique_id = file.file_unique_id
    user_state[user_id]["files"].append((file_id, file_unique_id))
    await update.message.reply_text("Файл получен.")

# Кнопки подтверждения/отмены
def confirmation_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("🔙 Назад", callback_data="cancel")]
    ])

# Обработка подтверждения/отмены
async def confirmation_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "confirm":
        await send_to_bitrix(user_id)
        await query.edit_message_text("Задача отправлена.")
        user_state.pop(user_id, None)
    elif query.data == "cancel":
        user_state.pop(user_id, None)
        await query.edit_message_text("Отменено.")

# Отправка задачи в Bitrix24
async def send_to_bitrix(user_id):
    data = user_state.get(user_id)
    if not data:
        return

    category = data["category"]
    text = data["text"]
    files = data["files"]

    # ID ответственного по категории
    responsible_map = {
        "1": 270,
        "2": 12,
        "other": 12
    }
    responsible_id = responsible_map.get(category, 12)

    # Создание задачи
    task_payload = {
        "fields": {
            "TITLE": "Новая задача из Telegram",
            "DESCRIPTION": text,
            "RESPONSIBLE_ID": responsible_id
        }
    }

    response = requests.post(BITRIX_WEBHOOK_URL, json=task_payload)
    response.raise_for_status()

# Обработка входящих обновлений от Telegram
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "OK"

# Установка вебхука при запуске
@app.before_first_request
def set_webhook():
    url = f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/webhook/{WEBHOOK_SECRET}"
    response = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={url}"
    )
    if not response.ok:
        logger.error("Failed to set webhook: %s", response.text)
    else:
        logger.info("Webhook set to: %s", url)

# Запуск Flask-сервера
if __name__ == "__main__":
    # Подключение хендлеров
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(category_selected, pattern="^category_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    application.add_handler(CallbackQueryHandler(confirmation_callback, pattern="^(confirm|cancel)$"))

    # Запуск приложения
    app.run(host="0.0.0.0", port=PORT)
