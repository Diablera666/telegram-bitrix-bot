import os
import logging
import asyncio
from threading import Thread
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import requests
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_SECRET", "defaultsecret")
PORT = int(os.getenv("PORT", 10000))
FOLDER_ID = 123  # Замените на реальный ID папки в Bitrix24

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация Flask
app = Flask(__name__)

# Инициализация бота
application = Application.builder().token(TOKEN).build()

# Хранилище данных пользователей
user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    keyboard = [["Вопрос 1", "Вопрос 2"], ["Вопрос 3", "Другое"]]
    reply_markup = {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}
    await update.message.reply_text(
        "Выберите категорию вопроса:",
        reply_markup=reply_markup
    )
    user_data[update.effective_user.id] = {'category': None, 'text': '', 'files': []}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id not in user_data:
        await start(update, context)
        return
        
    if text in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]:
        user_data[user_id]['category'] = text
        await update.message.reply_text(
            f"Выбрана категория: {text}\n"
            "Отправьте текст сообщения и/или файлы.\n"
            "Когда будете готовы, нажмите /confirm"
        )
    else:
        user_data[user_id]['text'] = text
        await update.message.reply_text("Текст сохранён. Можно добавить файлы или нажать /confirm")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка файлов"""
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
        
    if not file:
        await update.message.reply_text("Этот тип файла не поддерживается")
        return
        
    file_id = file.file_id
    file_name = getattr(file, 'file_name', f'file_{file_id[:8]}')
    
    if 'files' not in user_data[user_id]:
        user_data[user_id]['files'] = []
    
    user_data[user_id]['files'].append({
        'id': file_id,
        'name': file_name
    })
    
    await update.message.reply_text(f"Файл {file_name} добавлен")

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и создание задачи"""
    user_id = update.effective_user.id
    if user_id not in user_data:
        await start(update, context)
        return
        
    data = user_data[user_id]
    if not data['category']:
        await update.message.reply_text("Сначала выберите категорию")
        return
        
    if not data['text'] and not data.get('files'):
        await update.message.reply_text("Добавьте текст или файлы")
        return
        
    # Загрузка файлов в Bitrix24
    file_ids = []
    for file in data.get('files', []):
        try:
            file_obj = await application.bot.get_file(file['id'])
            file_bytes = await file_obj.download_as_bytearray()
            
            response = requests.post(
                f"{BITRIX_WEBHOOK}/disk.folder.uploadfile.json",
                params={'id': FOLDER_ID},
                files={'file': (file['name'], file_bytes)},
                timeout=30
            )
            
            if response.status_code == 200:
                file_id = response.json().get('result', {}).get('ID')
                if file_id:
                    file_ids.append(file_id)
        except Exception as e:
            logger.error(f"Ошибка загрузки файла: {e}")
    
    # Создание задачи
    task_data = {
        'fields': {
            'TITLE': f"{data['category']} от пользователя",
            'DESCRIPTION': data['text'],
            'RESPONSIBLE_ID': 270 if data['category'] in ["Вопрос 1", "Вопрос 3"] else 12,
            'UF_TASK_WEBDAV_FILES': file_ids
        }
    }
    
    try:
        response = requests.post(
            f"{BITRIX_WEBHOOK}/task.item.add.json",
            json=task_data,
            timeout=30
        )
        
        if response.status_code == 200:
            await update.message.reply_text("✅ Задача успешно создана!")
        else:
            await update.message.reply_text("❌ Ошибка при создании задачи")
            logger.error(f"Ошибка Bitrix24: {response.text}")
    except Exception as e:
        logger.error(f"Ошибка создания задачи: {e}")
        await update.message.reply_text("⚠️ Ошибка соединения с Bitrix24")
    
    # Очистка данных
    user_data.pop(user_id, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена создания задачи"""
    user_id = update.effective_user.id
    user_data.pop(user_id, None)
    await update.message.reply_text("Создание задачи отменено")

def run_async():
    """Запуск асинхронного event loop"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("confirm", confirm))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_file))
    
    # Запуск event loop
    loop.run_forever()

# Глобальная переменная для хранения event loop
bot_event_loop = None

@app.route(f"/webhook/{WEBHOOK_PATH}", methods=["POST"])
def webhook():
    """Обработчик вебхука"""
    if request.method == "POST":
        try:
            update = Update.de_json(request.json, application.bot)
            
            # Создаем задачу для обработки обновления
            async def process_update():
                await application.process_update(update)
            
            # Запускаем в существующем event loop
            asyncio.run_coroutine_threadsafe(
                process_update(),
                bot_event_loop
            )
            return "ok", 200
        except Exception as e:
            logger.error(f"Ошибка в обработке вебхука: {e}")
            return "error", 500
    return "Method not allowed", 405

@app.route("/")
def index():
    return "Bot is running and waiting for updates!"

if __name__ == "__main__":
    # Создаем и сохраняем event loop
    bot_event_loop = asyncio.new_event_loop()
    
    # Запускаем бота в отдельном потоке
    bot_thread = Thread(target=run_async, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask приложение
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
