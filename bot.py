import os
import logging
import requests
import hashlib
from flask import Flask, request
from telegram import (
    Bot, Update, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
import asyncio

# --- Конфигурация ---
TOKEN = "7407477056:AAEIfxS0wH56loSpTuNoE-cYTwVwRZPMl-U"
BITRIX_URL = "https://getman.bitrix24.kz/rest/270/1e5vf17l1tn1atcb/task.item.add.json"
BITRIX_UPLOAD_URL = "https://getman.bitrix24.kz/rest/270/1e5vf17l1tn1atcb/disk.folder.uploadfile.json"
BITRIX_FOLDER_ID = 123456  # замените на ваш ID папки

MAX_FILE_SIZE_MB = 50

WEBHOOK_SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"https://telegram-bitrix-bot.onrender.com{WEBHOOK_PATH}"

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Flask ---
app = Flask(__name__)

# --- Telegram Application ---
application = Application.builder().token(TOKEN).build()
bot = Bot(token=TOKEN)

# --- Хранилище пользователей ---
user_data = {}

# --- Команды ---
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Вопрос 1"), KeyboardButton("Вопрос 2")],
        [KeyboardButton("Вопрос 3"), KeyboardButton("Другое")],
    ],
    resize_keyboard=True,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"category": None, "text": None, "files": []}
    await update.message.reply_text("Выберите категорию:", reply_markup=MAIN_MENU)

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    category = update.message.text

    if category in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]:
        user_data[chat_id] = {"category": category, "text": None, "files": []}
        await update.message.reply_text(f"✍️ Введите текст задачи для категории «{category}»")
    else:
        await update.message.reply_text("Пожалуйста, выберите одну из опций в меню.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data or not user_data[chat_id]["category"]:
        await start(update, context)
        return

    if not user_data[chat_id]["text"]:
        user_data[chat_id]["text"] = update.message.text
        await update.message.reply_text("📎 Можете отправить файлы или нажмите /confirm для подтверждения.")
    else:
        await update.message.reply_text("Текст уже получен. Нажмите /confirm или отправьте файл.")

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data or not user_data[chat_id]["category"]:
        await start(update, context)
        return

    file = None
    if update.message.document:
        file = update.message.document
    elif update.message.photo:
        file = update.message.photo[-1]
    elif update.message.video:
        file = update.message.video
    elif update.message.audio:
        file = update.message.audio
    elif update.message.voice:
        file = update.message.voice
    elif update.message.video_note:
        file = update.message.video_note
    elif update.message.sticker:
        file = update.message.sticker

    if not file:
        await update.message.reply_text("❌ Формат не поддерживается.")
        return

    if file.file_size and file.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text("❌ Файл превышает лимит 50 МБ.")
        return

    os.makedirs("downloads", exist_ok=True)
    file_path = f"downloads/{chat_id}_{file.file_id}"
    await file.get_file().download_to_drive(file_path)

    user_data[chat_id]["files"].append(file_path)
    await update.message.reply_text(f"📥 Файл получен. Всего файлов: {len(user_data[chat_id]['files'])}")

async def delete_last_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    files = user_data.get(chat_id, {}).get("files", [])
    if files:
        last_file = files.pop()
        os.remove(last_file)
        await update.message.reply_text("🗑️ Последний файл удалён.")
    else:
        await update.message.reply_text("Нет файлов для удаления.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data.pop(chat_id, None)
    await update.message.reply_text("❌ Создание задачи отменено.", reply_markup=MAIN_MENU)

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = user_data.get(chat_id)
    if not data or not data["text"]:
        await update.message.reply_text("Нет текста для создания задачи.")
        return

    text = data["text"]
    category = data["category"]
    files = data["files"]
    responsible_id = 270 if category in ["Вопрос 1", "Вопрос 3"] else 12

    file_ids = []
    for path in files:
        filename = os.path.basename(path)
        with open(path, "rb") as f:
            response = requests.post(
                BITRIX_UPLOAD_URL,
                files={"file": (filename, f)},
                data={"id": BITRIX_FOLDER_ID, "generateUniqueName": "Y"},
            )
        result = response.json().get("result")
        if result and "ID" in result:
            file_ids.append(result["ID"])

    task_data = {
        "fields": {
            "TITLE": f"{category} от пользователя {chat_id}",
            "DESCRIPTION": text,
            "RESPONSIBLE_ID": responsible_id,
            "UF_TASK_WEBDAV_FILES": file_ids,
        }
    }

    task_response = requests.post(BITRIX_URL, json=task_data)
    if task_response.ok:
        await update.message.reply_text("✅ Задача успешно создана!", reply_markup=MAIN_MENU)
    else:
        await update.message.reply_text("❌ Ошибка при создании задачи.")

    user_data.pop(chat_id, None)

# --- Flask Webhook ---
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.run(application.process_update(update))
    return "OK"

# --- Регистрация обработчиков ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("cancel", cancel))
application.add_handler(CommandHandler("confirm", confirm))
application.add_handler(CommandHandler("delete", delete_last_file))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
application.add_handler(MessageHandler(filters.TEXT & filters.COMMAND, handle_text))
application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO |
                                       filters.AUDIO | filters.VOICE | filters.Sticker.ALL |
                                       filters.VIDEO_NOTE, file_handler))

# --- Установка вебхука ---
async def set_webhook():
    await bot.set_webhook(WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
    print("Webhook установлен:", WEBHOOK_URL)

if __name__ == "__main__":
    import threading
    threading.Thread(target=lambda: application.run_polling()).start()  # для dev
    asyncio.run(set_webhook())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
