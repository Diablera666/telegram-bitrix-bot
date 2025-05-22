import os
import logging
import asyncio

from flask import Flask, request
from telegram import Update, File as TelegramFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram import ReplyKeyboardMarkup
import requests
from dotenv import load_dotenv

load_dotenv()

# === Настройки ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK_URL")
BITRIX_USER_ID_270 = 270
BITRIX_USER_ID_12 = 12
FOLDER_ID = 123  # Укажи актуальный ID папки
SECRET_PATH = os.getenv("WEBHOOK_SECRET", "defaultsecret")
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# Flask приложение
app = Flask(__name__)

# Telegram bot
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Хранилище пользовательских данных
user_data = {}

# === Обработчики ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Вопрос 1", "Вопрос 2"], ["Вопрос 3", "Другое"]]
    await update.message.reply_text(
        "Выберите категорию:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    user_data[update.effective_user.id] = {
        "category": None,
        "text": "",
        "files": [],
    }

async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = update.message.text
    if category not in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]:
        await update.message.reply_text("Выберите категорию из меню.")
        return

    user_data[update.effective_user.id]["category"] = category
    await update.message.reply_text(
        "Отправьте текст и файлы, затем нажмите /confirm для подтверждения."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data:
        await update.message.reply_text("Сначала введите /start.")
        return

    user_data[uid]["text"] += update.message.text + "\n"

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data:
        await update.message.reply_text("Сначала введите /start.")
        return

    file = (
        update.message.document
        or update.message.photo[-1]
        or update.message.video
        or update.message.audio
        or update.message.voice
        or update.message.sticker
    )

    if not file:
        await update.message.reply_text("Неподдерживаемый тип файла.")
        return

    file_id = file.file_id
    tg_file: TelegramFile = await context.bot.get_file(file_id)

    if hasattr(file, "file_size") and file.file_size > MAX_FILE_SIZE_BYTES:
        await update.message.reply_text("Файл слишком большой. Максимум 50 МБ.")
        return

    file_bytes = await tg_file.download_as_bytes()
    filename = getattr(file, "file_name", f"{file_id}.bin")

    user_data[uid]["files"].append({"name": filename, "bytes": file_bytes})
    await update.message.reply_text(f"Файл '{filename}' добавлен.")

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data:
        await update.message.reply_text("Сначала введите /start.")
        return

    data = user_data[uid]
    category = data["category"]
    text = data["text"]
    files = data["files"]

    if not category or not text.strip():
        await update.message.reply_text("Пожалуйста, укажите категорию и текст.")
        return

    bitrix_user_id = BITRIX_USER_ID_270 if category in ["Вопрос 1", "Вопрос 3"] else BITRIX_USER_ID_12

    file_ids = []
    for file in files:
        response = requests.post(
            f"{BITRIX_WEBHOOK}/disk.folder.uploadfile.json",
            params={"id": FOLDER_ID},
            files={"file": (file["name"], file["bytes"])},
        )
        result = response.json().get("result")
        if result and "ID" in result:
            file_ids.append(result["ID"])

    task_data = {
        "fields": {
            "TITLE": f"{category} от {update.effective_user.first_name}",
            "DESCRIPTION": text,
            "RESPONSIBLE_ID": bitrix_user_id,
            "UF_TASK_WEBDAV_FILES": file_ids,
        }
    }

    res = requests.post(f"{BITRIX_WEBHOOK}/task.item.add.json", json=task_data)
    if "result" in res.json():
        await update.message.reply_text("Задача успешно создана ✅")
    else:
        await update.message.reply_text("Ошибка при создании задачи ❌")

    user_data.pop(uid)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data.pop(uid, None)
    await update.message.reply_text("Создание задачи отменено.")

# === Flask route для Telegram Webhook ===
@app.route(f"/webhook/{SECRET_PATH}", methods=["POST"])
def webhook():
    update_data = request.get_json(force=True)
    update = Update.de_json(update_data, application.bot)

    async def handle_update():
        if not application._initialized:
            await application.initialize()
        await application.process_update(update)

    asyncio.run(handle_update())
    return "OK", 200

# Health-check
@app.route("/")
def index():
    return "Bot is running", 200

# === Запуск Flask-сервера ===
if __name__ == "__main__":
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("confirm", confirm))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Sticker.ALL, handle_file))
    application.add_handler(MessageHandler(filters.TEXT, handle_text))

    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
