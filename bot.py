import os
import logging
import asyncio
from threading import Thread
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    ContextTypes,
    filters
)
import requests
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_SECRET", "defaultsecret")
PORT = int(os.getenv("PORT", 10000))
FOLDER_ID = 123  # Замените на ваш ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

user_data = {}

CATEGORY_BUTTONS = [["Вопрос 1", "Вопрос 2"], ["Вопрос 3", "Другое"]]
ACTION_BUTTONS = [["Подтвердить", "Отменить"], ["Удалить файл", "Назад"]]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(CATEGORY_BUTTONS, resize_keyboard=True)
    await update.message.reply_text("Выберите категорию:", reply_markup=keyboard)
    user_data[update.effective_user.id] = {'category': None, 'text': '', 'files': []}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in user_data:
        await start(update, context)
        return

    if text in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]:
        user_data[user_id]['category'] = text
        keyboard = ReplyKeyboardMarkup(ACTION_BUTTONS, resize_keyboard=True)
        await update.message.reply_text(
            f"Вы выбрали: {text}\nОтправьте сообщение и/или файлы.",
            reply_markup=keyboard
        )
    elif text == "Подтвердить":
        await confirm(update, context)
    elif text == "Отменить":
        await cancel(update, context)
    elif text == "Удалить файл":
        await delete_last_file(update)
    elif text == "Назад":
        await start(update, context)
    else:
        # Добавляем к существующему тексту
        user_data[user_id]['text'] += f"\n{text}"
        await update.message.reply_text("Добавлен текст. Можно отправить ещё или нажать Подтвердить.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await start(update, context)
        return

    file = None
    if update.message.document:
        file = update.message.document
    elif update.message.photo:
        file = update.message.photo[-1]
    elif update.message.video:
        file = update.message.video
    else:
        await update.message.reply_text("Тип файла не поддерживается.")
        return

    file_id = file.file_id
    file_name = getattr(file, 'file_name', f'file_{file_id[:8]}')

    user_data[user_id]['files'].append({
        'id': file_id,
        'name': file_name
    })

    await update.message.reply_text(f"Файл {file_name} добавлен.")


async def delete_last_file(update: Update):
    user_id = update.effective_user.id
    files = user_data.get(user_id, {}).get('files', [])
    if files:
        removed = files.pop()
        await update.message.reply_text(f"Удалён последний файл: {removed['name']}")
    else:
        await update.message.reply_text("Файлов нет.")


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id)

    if not data or not data['category']:
        await update.message.reply_text("Сначала выберите категорию.")
        return

    if not data['text'] and not data['files']:
        await update.message.reply_text("Добавьте текст или файлы.")
        return

    file_ids = []
    for file in data.get('files', []):
        try:
            tg_file = await application.bot.get_file(file['id'])
            file_bytes = await tg_file.download_as_bytearray()

            response = requests.post(
                f"{BITRIX_WEBHOOK}disk.folder.uploadfile.json",
                params={'id': FOLDER_ID},
                files={'file': (file['name'], file_bytes)},
                timeout=30
            )

            result = response.json()
            if response.status_code == 200 and result.get("result", {}).get("ID"):
                file_ids.append(result["result"]["ID"])
            else:
                logger.error(f"Ошибка загрузки файла: {result}")
        except Exception as e:
            logger.error(f"Ошибка загрузки файла: {e}")

    responsible_id = 270 if data['category'] in ["Вопрос 1", "Вопрос 3"] else 12
    task_data = {
        'data': {
            'TITLE': f"{data['category']} от пользователя",
            'DESCRIPTION': data['text'],
            'RESPONSIBLE_ID': responsible_id,
            'UF_TASK_WEBDAV_FILES': file_ids
        }
    }

    try:
        response = requests.post(
            f"{BITRIX_WEBHOOK}task.add",
            json=task_data,
            timeout=30
        )
        result = response.json()
        if response.status_code == 200 and result.get("result"):
            await update.message.reply_text("✅ Задача успешно создана!")
        else:
            await update.message.reply_text("❌ Ошибка при создании задачи")
            logger.error(f"Ошибка Bitrix24: {response.text}")
    except Exception as e:
        logger.error(f"Ошибка соединения с Bitrix24: {e}")
        await update.message.reply_text("⚠️ Ошибка соединения с Bitrix24")

    user_data.pop(user_id, None)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data.pop(user_id, None)
    await update.message.reply_text("Создание задачи отменено.")
    await start(update, context)


def run_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_file))

    loop.run_forever()


bot_event_loop = None


@app.route(f"/webhook/{WEBHOOK_PATH}", methods=["POST"])
def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.json, application.bot)

            async def process_update():
                await application.process_update(update)

            asyncio.run_coroutine_threadsafe(
                process_update(),
                bot_event_loop
            )
            return "ok", 200
        except Exception as e:
            logger.error(f"Ошибка в webhook: {e}")
            return "error", 500
    return "Method Not Allowed", 405


@app.route("/")
def index():
    return "Bot is running"


if __name__ == "__main__":
    bot_event_loop = asyncio.new_event_loop()
    bot_thread = Thread(target=run_async, daemon=True)
    bot_thread.start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
