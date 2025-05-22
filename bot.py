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
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)

# Telegram bot
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Хранилище пользовательских данных
user_data = {}

async def initialize_app():
    """Инициализация приложения"""
    await application.initialize()
    await application.start()
    logger.info("Application initialized")

def run_async(coro):
    """Запуск асинхронной функции из синхронного кода"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

# Инициализация при запуске
run_async(initialize_app())

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
    uid = update.effective_user.id
    category = update.message.text
    
    if category not in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]:
        await update.message.reply_text("Выберите категорию из меню.")
        return

    if uid not in user_data:
        user_data[uid] = {"category": None, "text": "", "files": []}

    user_data[uid]["category"] = category
    await update.message.reply_text(
        "Отправьте текст и файлы, затем нажмите /confirm для подтверждения."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data or user_data[uid]["category"] is None:
        await update.message.reply_text("Сначала выберите категорию (/start).")
        return

    user_data[uid]["text"] += update.message.text + "\n"
    await update.message.reply_text("Текст добавлен. Можете отправить еще или файлы.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data:
        await update.message.reply_text("Сначала введите /start.")
        return

    file = (
        update.message.document
        or (update.message.photo[-1] if update.message.photo else None)
        or update.message.video
    )

    if not file:
        await update.message.reply_text("Неподдерживаемый тип файла.")
        return

    try:
        file_id = file.file_id
        tg_file: TelegramFile = await context.bot.get_file(file_id)

        if hasattr(file, "file_size") and file.file_size > MAX_FILE_SIZE_BYTES:
            await update.message.reply_text("Файл слишком большой. Максимум 50 МБ.")
            return

        file_bytes = await tg_file.download_as_bytes()
        filename = getattr(file, "file_name", f"file_{file_id[:8]}.bin")

        if uid not in user_data:
            user_data[uid] = {"category": None, "text": "", "files": []}

        user_data[uid]["files"].append({"name": filename, "bytes": file_bytes})
        await update.message.reply_text(f"Файл '{filename}' успешно добавлен.")

    except Exception as e:
        logger.error(f"Ошибка обработки файла: {e}")
        await update.message.reply_text("Произошла ошибка при обработке файла.")

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data:
        await update.message.reply_text("Сначала введите /start.")
        return

    data = user_data[uid]
    category = data["category"]
    text = data["text"].strip()
    files = data["files"]

    if not category:
        await update.message.reply_text("Пожалуйста, выберите категорию (/start).")
        return

    if not text and not files:
        await update.message.reply_text("Добавьте текст или файлы перед подтверждением.")
        return

    bitrix_user_id = BITRIX_USER_ID_270 if category in ["Вопрос 1", "Вопрос 3"] else BITRIX_USER_ID_12

    try:
        file_ids = []
        for file in files:
            try:
                response = requests.post(
                    f"{BITRIX_WEBHOOK}/disk.folder.uploadfile.json",
                    params={"id": FOLDER_ID},
                    files={"file": (file["name"], file["bytes"])},
                    timeout=30
                )
                result = response.json().get("result")
                if result and "ID" in result:
                    file_ids.append(result["ID"])
            except Exception as e:
                logger.error(f"Ошибка загрузки файла в Bitrix24: {e}")

        username = update.effective_user.first_name or update.effective_user.username or "Пользователь"
        task_data = {
            "fields": {
                "TITLE": f"{category} от {username}",
                "DESCRIPTION": text,
                "RESPONSIBLE_ID": bitrix_user_id,
                "UF_TASK_WEBDAV_FILES": file_ids,
            }
        }

        res = requests.post(f"{BITRIX_WEBHOOK}/task.item.add.json", json=task_data, timeout=30)
        if res.status_code == 200 and "result" in res.json():
            await update.message.reply_text("✅ Задача успешно создана в Bitrix24!")
        else:
            logger.error(f"Ошибка создания задачи: {res.text}")
            await update.message.reply_text("❌ Ошибка при создании задачи")

    except Exception as e:
        logger.error(f"Ошибка при работе с Bitrix24: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при создании задачи")

    finally:
        user_data.pop(uid, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data.pop(uid, None)
    await update.message.reply_text("Создание задачи отменено.")

# === Flask route для Telegram Webhook ===
@app.route(f"/webhook/{SECRET_PATH}", methods=["POST"])
def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(), application.bot)
            
            # Создаем новую задачу для обработки обновления
            async def process_update():
                await application.update_queue.put(update)
            
            run_async(process_update())
            return "OK", 200
        except Exception as e:
            logger.error(f"Ошибка в обработке вебхука: {e}")
            return "Error", 500
    return "Method not allowed", 405

# Health-check
@app.route("/")
def index():
    return "Bot is running", 200

# Регистрация обработчиков
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("confirm", confirm))
application.add_handler(CommandHandler("cancel", cancel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^(Вопрос 1|Вопрос 2|Вопрос 3|Другое)$'), handle_text))
application.add_handler(MessageHandler(filters.Regex(r'^(Вопрос 1|Вопрос 2|Вопрос 3|Другое)$'), handle_category))
application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_file))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
