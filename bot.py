import os
import logging
import requests
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_TASK = os.getenv("BITRIX_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
BITRIX_UPLOAD_URL = BITRIX_WEBHOOK_TASK.replace('task.item.add.json', 'disk.folder.uploadfile.json')
BITRIX_FOLDER_ID = 5636

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_data = {}

CATEGORY_MAP = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

bot = Bot(token=TOKEN)
app = Flask(__name__)

# Обработчики теперь должны быть async, а context — ContextTypes.DEFAULT_TYPE

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(k, callback_data=k)] for k in CATEGORY_MAP]
    await update.message.reply_text(
        "Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    user_data[update.effective_chat.id] = {"files": [], "text": "", "category": None}

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data in CATEGORY_MAP:
        user_data[query.message.chat.id]["category"] = query.data
        await query.edit_message_text(
            f"Категория: {query.data}\nОтправьте описание и/или файлы."
        )
    elif query.data == "confirm":
        await confirm_task(update, context)
    elif query.data == "cancel":
        await cancel_task(update, context)
    elif query.data == "remove_last":
        await remove_last_file(update, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data:
        await start(update, context)
        return

    user_data[chat_id]["text"] = update.message.text
    await show_preview(update, chat_id)

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data:
        await start(update, context)
        return

    file = None
    if update.message.document:
        file = update.message.document
    elif update.message.photo:
        file = update.message.photo[-1]
    elif update.message.video:
        file = update.message.video

    if not file:
        await update.message.reply_text("Формат файла не поддерживается.")
        return

    try:
        file_path = f"tmp_{chat_id}_{file.file_id}"
        await file.get_file().download_to_drive(file_path)
        user_data[chat_id]["files"].append(file_path)
        await update.message.reply_text(f"Файл получен. Всего: {len(user_data[chat_id]['files'])}")
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}")
        await update.message.reply_text("Ошибка обработки файла.")

async def show_preview(update, chat_id):
    data = user_data[chat_id]
    text = f"Категория: {data['category']}\nОписание: {data['text']}\nФайлов: {len(data['files'])}"

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    if data["files"]:
        keyboard.append([InlineKeyboardButton("🗑 Удалить последний файл", callback_data="remove_last")])

    if hasattr(update, 'message'):
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    data = user_data.get(chat_id)

    if not data or not data["category"]:
        await query.edit_message_text("Ошибка: нет данных задачи")
        return

    file_ids = []
    for file_path in data["files"]:
        try:
            file_id = upload_to_bitrix(file_path)
            if file_id:
                file_ids.append(file_id)
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Ошибка загрузки файла {file_path}: {e}")

    task_data = {
        "fields": {
            "TITLE": f"Запрос из Telegram: {data['category']}",
            "DESCRIPTION": data["text"],
            "RESPONSIBLE_ID": CATEGORY_MAP[data["category"]],
            "UF_TASK_WEBDAV_FILES": file_ids
        }
    }

    try:
        response = requests.post(BITRIX_WEBHOOK_TASK, json=task_data, timeout=10)
        if response.status_code == 200:
            await query.edit_message_text("✅ Задача успешно создана!")
        else:
            logger.error(f"Ошибка Bitrix: {response.text}")
            await query.edit_message_text("❌ Ошибка при создании задачи")
    except Exception as e:
        logger.error(f"Ошибка запроса: {e}")
        await query.edit_message_text("⚠️ Ошибка соединения с Bitrix24")

    user_data.pop(chat_id, None)

def upload_to_bitrix(file_path):
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                BITRIX_UPLOAD_URL,
                files={'file': (os.path.basename(file_path), f)},
                data={'id': BITRIX_FOLDER_ID},
                timeout=30
            )
            result = response.json()
            logger.info(f"Upload response: {result}")
            return result.get('result', {}).get('ID')
    except Exception as e:
        logger.error(f"Ошибка загрузки в Bitrix: {e}")
        return None

async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id

    if chat_id in user_data:
        for file_path in user_data[chat_id]["files"]:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
        user_data.pop(chat_id)

    await query.edit_message_text("Создание задачи отменено.")

async def remove_last_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id

    if chat_id in user_data and user_data[chat_id]["files"]:
        last_file = user_data[chat_id]["files"].pop()
        try:
            if os.path.exists(last_file):
                os.remove(last_file)
        except:
            pass
        await show_preview(update, chat_id)
    else:
        await query.answer("Нет файлов для удаления")

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), bot)
    # Для async нужно использовать loop.run_until_complete
    import asyncio
    asyncio.get_event_loop().run_until_complete(application.process_update(update))
    return "ok"

@app.route("/")
def index():
    return "Telegram Bot is running!"

if __name__ == "__main__":
    import asyncio

    os.makedirs("downloads", exist_ok=True)

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, file_handler))

    bot.delete_webhook()
    bot.set_webhook(url=f"https://telegram-bitrix-bot.onrender.com/webhook/{TOKEN}")

    # Запускаем Flask
    app.run(host="0.0.0.0", port=PORT)
