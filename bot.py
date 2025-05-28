import os
import logging
import requests
from flask import Flask, request
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8443))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# Временное хранилище сессий пользователей
user_sessions = {}

# Категории и ID ответственных
CATEGORIES = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(text, callback_data=f"category|{text}")] for text in CATEGORIES]
    await update.message.reply_text(
        "Привет! Я бот для создания задач в Bitrix24. Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Обработка нажатий кнопок
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data.startswith("category|"):
        category = query.data.split("|", 1)[1]
        user_sessions[user_id] = {
            "category": category,
            "text": None,
            "files": []
        }
        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
            [InlineKeyboardButton("🗑 Удалить последний файл", callback_data="delete_last")],
            [InlineKeyboardButton("❌ Отменить", callback_data="cancel")],
            [InlineKeyboardButton("🔙 Вернуться назад в меню", callback_data="back")]
        ]
        await query.message.reply_text(
            f"Вы выбрали категорию: {category}\n\nОтправьте, пожалуйста, текст и файлы. После этого нажмите 'Подтвердить'.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "confirm":
        await send_to_bitrix(update, context)

    elif query.data == "cancel":
        user_sessions.pop(user_id, None)
        await query.message.reply_text("Создание задачи отменено.")
        await start(update, context)

    elif query.data == "back":
        user_sessions.pop(user_id, None)
        await start(update, context)

    elif query.data == "delete_last":
        session = user_sessions.get(user_id)
        if session and session['files']:
            session['files'].pop()
            await query.message.reply_text("Последний файл удалён.")
        else:
            await query.message.reply_text("Нет файлов для удаления.")

# Приём текста
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(update.message.from_user.id)
    if session is not None:
        session["text"] = update.message.text
        await update.message.reply_text("Текст сохранён. Отправьте файлы или нажмите 'Подтвердить'.")

# Приём файлов
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(update.message.from_user.id)
    if session is None:
        return

    file = None
    for kind in ('document', 'photo', 'video', 'audio', 'voice', 'sticker'):
        file = getattr(update.message, kind, None)
        if file:
            if kind == 'photo':
                file = file[-1]
            break

    if not file:
        return

    telegram_file = await context.bot.get_file(file.file_id)
    file_info = {
        "file_id": file.file_id,
        "file_path": telegram_file.file_path,
        "file_unique_id": file.file_unique_id,
        "mime_type": getattr(file, 'mime_type', None),
        "file_name": getattr(file, 'file_name', None)
    }
    session["files"].append(file_info)
    await update.message.reply_text("Файл добавлен. Можете продолжить или нажмите 'Подтвердить'.")

# Отправка задачи в Bitrix24
async def send_to_bitrix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await update.callback_query.message.reply_text("Сессия не найдена.")
        return

    files_bitrix_ids = []
    for file in session["files"]:
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file['file_path']}"
        bitrix_file_id = upload_file_to_bitrix(file_url, file.get("file_name") or file["file_unique_id"])
        if bitrix_file_id:
            files_bitrix_ids.append(bitrix_file_id)

    task_data = {
        "fields": {
            "TITLE": f"Задача от Telegram-бота ({session['category']})",
            "DESCRIPTION": session["text"] or "(без описания)",
            "RESPONSIBLE_ID": CATEGORIES[session["category"]],
            "UF_TASK_WEBDAV_FILES": files_bitrix_ids
        }
    }

    try:
        response = requests.post(BITRIX_WEBHOOK_URL, json=task_data)
        response.raise_for_status()
        await update.callback_query.message.reply_text("Задача успешно создана в Bitrix24!")
    except Exception as e:
        logger.error("Ошибка при создании задачи", exc_info=e)
        await update.callback_query.message.reply_text("Ошибка при создании задачи в Bitrix24.")
    
    user_sessions.pop(user_id, None)

# Загрузка файла в Bitrix24
def upload_file_to_bitrix(file_url, filename):
    folder_id = "0"
    upload_url = BITRIX_WEBHOOK_URL.replace("task.item.add.json", "disk.folder.uploadfile.json")
    data = {"id": folder_id, "generateUniqueName": "Y"}

    try:
        with requests.get(file_url, stream=True) as tg_resp:
            tg_resp.raise_for_status()
            files = {"file": (filename, tg_resp.raw)}
            response = requests.post(upload_url, data=data, files=files)
            response.raise_for_status()
            result = response.json()
            return result.get("result", {}).get("ID")
    except Exception as e:
        logger.error("Ошибка загрузки файла в Bitrix", exc_info=e)
        return None

# Обработчик Webhook от Telegram
@app.route(f"/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    application.create_task(
        application.update_queue.put(
            Update.de_json(request.get_json(force=True), application.bot)
        )
    )
    return "ok", 200


# Корневая страница
@app.route("/", methods=["GET"])
def index():
    return "OK", 200

# Регистрация хендлеров
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_file))

# Запуск
if __name__ == "__main__":
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{WEBHOOK_SECRET}"
    logger.info(f"Setting webhook to: {webhook_url}")

    import asyncio
    asyncio.run(application.bot.set_webhook(url=webhook_url))

    app.run(host="0.0.0.0", port=PORT)
