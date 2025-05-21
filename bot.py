import os
import logging
import requests
import time
import random
import shutil
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes
)
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_TASK = os.getenv("BITRIX_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
BITRIX_UPLOAD_URL = BITRIX_WEBHOOK_TASK.replace('task.item.add.json', 'disk.folder.uploadfile.json')
BITRIX_FOLDER_ID = 5636  # ID папки для загрузки файлов

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация приложения
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# Хранение данных пользователей
user_data = {}

# Соответствие категорий и ответственных
CATEGORY_MAP = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    try:
        chat_id = update.effective_chat.id
        keyboard = [[InlineKeyboardButton(k, callback_data=k)] for k in CATEGORY_MAP]
        
        await update.message.reply_text(
            "Привет! Я бот для создания задач в Bitrix24.\n"
            "Выберите категорию вопроса:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        user_data[chat_id] = {
            "files": [],
            "text": "",
            "category": None
        }
        logger.info(f"Новый диалог с пользователем {chat_id}")
        
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline-кнопок"""
    try:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat.id

        if query.data in CATEGORY_MAP:
            user_data[chat_id] = {
                "files": [],
                "text": "",
                "category": query.data
            }
            await query.edit_message_text(
                f"Вы выбрали: <b>{query.data}</b>\n\n"
                "Пришлите текст сообщения или файлы (фото, видео, документы).\n"
                "Когда закончите, нажмите «✅ Подтвердить».",
                parse_mode="HTML"
            )
            logger.info(f"Пользователь {chat_id} выбрал категорию: {query.data}")

        elif query.data == "confirm":
            await confirm_task(update, context)
        elif query.data == "cancel":
            await cancel_task(update, context)
        elif query.data == "remove_last":
            await remove_last_file(update, context)

    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        await query.edit_message_text("⚠️ Произошла ошибка. Попробуйте снова.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    try:
        chat_id = update.effective_chat.id
        if chat_id not in user_data or not user_data[chat_id]["category"]:
            await start(update, context)
            return

        user_data[chat_id]["text"] = update.message.text
        await show_preview(update, chat_id)
        logger.info(f"Получен текст от {chat_id}")

    except Exception as e:
        logger.error(f"Ошибка в text_handler: {e}")
        await update.message.reply_text("⚠️ Не удалось обработать текст. Попробуйте снова.")

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка файлов"""
    try:
        chat_id = update.effective_chat.id
        if chat_id not in user_data or not user_data[chat_id]["category"]:
            await start(update, context)
            return

        # Определяем тип файла
        file = None
        if update.message.document:
            file = update.message.document
        elif update.message.photo:
            file = update.message.photo[-1]  # Берем самое высокое качество
        elif update.message.video:
            file = update.message.video

        if not file:
            await update.message.reply_text("❌ Этот формат файла не поддерживается.")
            return

        # Скачиваем файл
        os.makedirs("downloads", exist_ok=True)
        file_path = f"downloads/{chat_id}_{file.file_id}"
        await file.get_file().download_to_drive(file_path)
        
        user_data[chat_id]["files"].append(file_path)
        await update.message.reply_text(
            f"📎 Файл получен. Всего файлов: {len(user_data[chat_id]['files'])}"
        )
        logger.info(f"Файл сохранен: {file_path}")

    except Exception as e:
        logger.error(f"Ошибка в file_handler: {e}")
        await update.message.reply_text("⚠️ Не удалось обработать файл. Попробуйте снова.")

async def show_preview(update: Update, chat_id: int):
    """Показ превью задачи"""
    try:
        data = user_data[chat_id]
        text = (
            f"<b>Превью задачи</b>\n\n"
            f"Категория: {data['category']}\n"
            f"Текст: {data['text'] or 'не указан'}\n"
            f"Файлов: {len(data['files'])}"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
        if data["files"]:
            keyboard.append([InlineKeyboardButton("🗑 Удалить последний файл", callback_data="remove_last")])

        if update.message:
            await update.message.reply_text(text, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        else:
            await update.callback_query.edit_message_text(text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Ошибка в show_preview: {e}")

async def confirm_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание задачи в Bitrix24"""
    try:
        query = update.callback_query
        chat_id = query.message.chat.id
        data = user_data.get(chat_id)

        if not data or not data["category"]:
            await query.edit_message_text("❌ Нет данных для создания задачи")
            return

        # Загрузка файлов в Bitrix24
        file_ids = []
        for file_path in data["files"]:
            try:
                file_id = upload_to_bitrix(file_path)
                if file_id:
                    file_ids.append(file_id)
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Ошибка загрузки файла {file_path}: {e}")

        # Создание задачи
        task_data = {
            "fields": {
                "TITLE": f"Telegram: {data['category']}",
                "DESCRIPTION": data["text"] or "Без описания",
                "RESPONSIBLE_ID": CATEGORY_MAP[data["category"]],
                "UF_TASK_WEBDAV_FILES": file_ids
            }
        }

        response = requests.post(BITRIX_WEBHOOK_TASK, json=task_data, timeout=15)
        if response.status_code == 200:
            await query.edit_message_text("✅ Задача успешно создана в Bitrix24!")
            logger.info(f"Задача создана для {chat_id}")
        else:
            logger.error(f"Ошибка Bitrix: {response.status_code} - {response.text}")
            await query.edit_message_text("❌ Ошибка при создании задачи")

        # Очистка данных
        user_data.pop(chat_id, None)

        # Периодическая очистка папки downloads
        if random.random() < 0.1:  # 10% chance
            clean_downloads()

    except Exception as e:
        logger.error(f"Ошибка в confirm_task: {e}")
        await query.edit_message_text("⚠️ Произошла ошибка. Попробуйте позже.")

def upload_to_bitrix(file_path: str) -> Optional[int]:
    """Загрузка файла на диск Bitrix24"""
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                BITRIX_UPLOAD_URL,
                files={'file': (os.path.basename(file_path), f)},
                data={'id': BITRIX_FOLDER_ID},
                timeout=30
            )
            result = response.json()
            logger.info(f"Ответ Bitrix: {result}")
            return result.get('result', {}).get('ID')
    except Exception as e:
        logger.error(f"Ошибка загрузки в Bitrix: {e}")
        return None

async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена создания задачи"""
    try:
        query = update.callback_query
        chat_id = query.message.chat.id

        # Удаление временных файлов
        if chat_id in user_data:
            for file_path in user_data[chat_id]["files"]:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception:
                    pass
            user_data.pop(chat_id)

        await query.edit_message_text("❌ Создание задачи отменено.")
        logger.info(f"Пользователь {chat_id} отменил создание задачи")

    except Exception as e:
        logger.error(f"Ошибка в cancel_task: {e}")

async def remove_last_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление последнего файла"""
    try:
        query = update.callback_query
        chat_id = query.message.chat.id

        if chat_id in user_data and user_data[chat_id]["files"]:
            last_file = user_data[chat_id]["files"].pop()
            try:
                if os.path.exists(last_file):
                    os.remove(last_file)
            except Exception:
                pass
            await show_preview(update, chat_id)
            logger.info(f"Удален файл {last_file}")
        else:
            await query.answer("Нет файлов для удаления")

    except Exception as e:
        logger.error(f"Ошибка в remove_last_file: {e}")

def clean_downloads():
    """Очистка папки downloads"""
    try:
        for filename in os.listdir("downloads"):
            file_path = os.path.join("downloads", filename)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                logger.error(f"Ошибка удаления {file_path}: {e}")
        logger.info("Папка downloads очищена")
    except Exception as e:
        logger.error(f"Ошибка очистки downloads: {e}")

# Вебхук для Flask
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    if request.method == "POST":
        try:
            json_data = request.get_json()
            update = Update.de_json(json_data, application.bot)
            application.create_task(application.update_queue.put(update))
            return "ok"
        except Exception as e:
            logger.error(f"Ошибка вебхука: {e}")
            return "error", 500
    return "Method not allowed", 405

@app.route("/")
def index():
    return "Telegram Bot is running and waiting for updates!"

def setup_handlers():
    """Регистрация обработчиков"""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, file_handler))

def main():
    """Основная функция запуска"""
    try:
        # Проверка переменных окружения
        if not TOKEN or not BITRIX_WEBHOOK_TASK:
            raise ValueError("Не заданы обязательные переменные окружения")

        logger.info("Настройка бота...")
        setup_handlers()
        
        # Создаем папку для загрузок
        os.makedirs("downloads", exist_ok=True)
        
        # Установка вебхука
        logger.info("Установка вебхука...")
        application.bot.delete_webhook()
        time.sleep(1)
        application.bot.set_webhook(
            url=f"https://telegram-bitrix-bot.onrender.com/webhook/{TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
        
        logger.info(f"Запуск Flask на порту {PORT}...")
        app.run(host="0.0.0.0", port=PORT)

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    main()
