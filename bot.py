import os
import logging
import requests
from flask import Flask, request
from telegram import (
    Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
)
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_TASK = os.getenv("BITRIX_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
BITRIX_UPLOAD_URL = "https://getman.bitrix24.kz/rest/270/1e5vf17l1tn1atcb/disk.folder.uploadfile.json"
BITRIX_FOLDER_ID = 5636  # ID публичной папки

bot = Bot(token=TOKEN)
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=0)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

user_data = {}

CATEGORY_MAP = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

def start(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton(k, callback_data=k)] for k in CATEGORY_MAP.keys()]
    update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))
    user_data[update.message.chat_id] = {"files": [], "text": "", "category": None}

def button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    category = query.data
    user_data[query.message.chat_id]["category"] = category
    query.edit_message_text(f"Категория: {category}\nВведите описание задачи или отправьте файлы.")

def text_handler(update: Update, context: CallbackContext):
    data = user_data.get(update.message.chat_id, {})
    data["text"] = update.message.text
    show_confirm_menu(update, context, data)

def show_confirm_menu(update, context, data):
    files_count = len(data["files"])
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        [InlineKeyboardButton("🗑 Удалить последний файл", callback_data="remove_last")] if files_count else []
    ]
    text_preview = f"Описание: {data['text']}\nФайлов: {files_count}"
    update.message.reply_text(text_preview, reply_markup=InlineKeyboardMarkup(keyboard))

def file_handler(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    file_id = None

    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.video:
        file_id = update.message.video.file_id

    if not file_id:
        update.message.reply_text("Тип файла не поддерживается.")
        return

    file = bot.get_file(file_id)
    file_path = f"downloads/{chat_id}_{file_id}"
    os.makedirs("downloads", exist_ok=True)
    file.download(file_path)

    user_data[chat_id]["files"].append(file_path)
    update.message.reply_text(f"Файл добавлен. Всего файлов: {len(user_data[chat_id]['files'])}")

def confirm(update: Update, context: CallbackContext):
    query = update.callback_query
    chat_id = query.message.chat_id
    data = user_data.get(chat_id)

    if not data:
        query.edit_message_text("Нет данных для создания задачи.")
        return

    bitrix_file_ids = []
    for path in data["files"]:
        file_id = upload_file_to_bitrix(path)
        if file_id:
            bitrix_file_ids.append(file_id)

    task_data = {
        "fields": {
            "TITLE": f"Задача от Telegram ({data['category']})",
            "RESPONSIBLE_ID": CATEGORY_MAP[data["category"]],
            "DESCRIPTION": data["text"],
            "UF_TASK_WEBDAV_FILES": bitrix_file_ids
        }
    }

    response = requests.post(BITRIX_WEBHOOK_TASK, json=task_data)
    logging.info(f"Ответ создания задачи: {response.status_code}, {response.text}")
    query.edit_message_text("Задача создана.")
    user_data.pop(chat_id, None)

def upload_file_to_bitrix(filepath):
    filename = os.path.basename(filepath)
    step1_payload = {
        "id": BITRIX_FOLDER_ID,
        "generateUniqueName": "Y",
        "name": filename
    }

    step1 = requests.post(BITRIX_UPLOAD_URL, json=step1_payload)
    result = step1.json().get("result", {})
    upload_url = result.get("uploadUrl")
    if not upload_url:
        logging.warning(f"Не удалось получить uploadUrl из ответа: {step1.text}")
        return None

    with open(filepath, "rb") as f:
        upload_response = requests.post(upload_url, files={"file": f})
        upload_result = upload_response.json()
        file_id = upload_result.get("result", {}).get("ID")
        logging.info(f"Bitrix upload result: {upload_result}")
        if not file_id:
            logging.warning(f"Не удалось получить ID файла из ответа: {upload_result}")
        return file_id

def cancel(update: Update, context: CallbackContext):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_data.pop(chat_id, None)
    query.edit_message_text("Отменено.")

def remove_last_file(update: Update, context: CallbackContext):
    query = update.callback_query
    chat_id = query.message.chat_id
    data = user_data.get(chat_id)
    if data and data["files"]:
        removed = data["files"].pop()
        if os.path.exists(removed):
            os.remove(removed)
        query.edit_message_text(f"Удалён последний файл. Осталось: {len(data['files'])}")
    else:
        query.edit_message_text("Нет файлов для удаления.")

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

@app.route("/", methods=["GET"])
def index():
    return "Bot is running."

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(confirm, pattern="^confirm$"))
dispatcher.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))
dispatcher.add_handler(CallbackQueryHandler(remove_last_file, pattern="^remove_last$"))
dispatcher.add_handler(CallbackQueryHandler(button))
dispatcher.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
dispatcher.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, file_handler))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
