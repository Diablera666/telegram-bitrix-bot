import os
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Union
from flask import Flask
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery
)
import requests
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Константы
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN в переменных окружения")

BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
if not BITRIX_WEBHOOK_URL:
    raise ValueError("Не задан BITRIX_WEBHOOK_URL в переменных окружения")

CREATOR_ID = 12  # ID постановщика задачи в Битrix24
PARENT_ID = 5636  # Папка "Загруженные файлы" на общем диске Bitrix24

# ID ответственных в зависимости от типа вопроса
RESPONSIBLE_IDS = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

# Инициализация бота
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# Типы для аннотаций
UserState = Dict[str, Union[str, List[str]]]
StatesDict = Dict[int, UserState]

# Состояния пользователей
user_state: StatesDict = {}
last_callback_time: Dict[int, float] = {}

def add_workdays(start_date: datetime, workdays: int) -> datetime:
    cur = start_date
    added = 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur

def file_link(message: Message) -> Optional[str]:
    try:
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
        elif message.content_type == "video":
            file_id = message.video.file_id
        elif message.content_type == "document":
            file_id = message.document.file_id
        else:
            logger.warning(f"Unsupported content type: {message.content_type}")
            return None

        info = bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"
    except Exception as e:
        logger.error(f"Error getting file link: {e}")
        return None

def upload_file_to_bitrix(file_url: str, folder_id: int = PARENT_ID) -> Optional[int]:
    local_filename = None
    try:
        # Скачиваем файл временно
        local_filename = file_url.split('/')[-1].split('?')[0]
        with requests.get(file_url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Загружаем в Bitrix24
        upload_url = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.folder.uploadfile.json')
        
        with open(local_filename, 'rb') as f:
            files = {'file': (os.path.basename(local_filename), f)}
            data = {'id': folder_id}
            
            response = requests.post(upload_url, data=data, files=files, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Bitrix24 upload response: {result}")

            # Обрабатываем разные форматы ответов
            if 'result' in result:
                if 'ATTACHED_OBJECT' in result['result']:
                    return result['result']['ATTACHED_OBJECT']['ID']
                elif 'attachedId' in result['result']:
                    return result['result']['attachedId']
                elif 'ID' in result['result']:
                    return result['result']['ID']
            
            logger.error(f"No file ID in response: {result}")
            return None

    except Exception as e:
        logger.error(f"Error uploading file to Bitrix24: {e}")
        return None
    finally:
        if local_filename and os.path.exists(local_filename):
            try:
                os.remove(local_filename)
            except Exception as e:
                logger.error(f"Error removing temp file: {e}")

def create_bitrix_task(title: str, description: str, responsible_id: int, 
                     attached_ids: Optional[List[int]] = None) -> bool:
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')
    fields = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": responsible_id,
        "CREATED_BY": CREATOR_ID,
        "DEADLINE": deadline
    }
    
    if attached_ids:
        fields["UF_TASK_WEBDAV_FILES"] = [str(id) for id in attached_ids]
        logger.info(f"Attaching files: {attached_ids}")

    try:
        response = requests.post(
            BITRIX_WEBHOOK_URL,
            json={"fields": fields},
            timeout=15
        )
        logger.info(f"Task creation response: {response.status_code}, {response.text}")
        
        return response.status_code == 200
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return False

def create_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("Вопрос 1"),
        KeyboardButton("Вопрос 2"),
        KeyboardButton("Вопрос 3"),
        KeyboardButton("Другое")
    )
    return kb

def create_finish_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data="ok"),
        InlineKeyboardButton("❌ Отменить", callback_data="cancel"),
        InlineKeyboardButton("🗑️ Удалить последний файл", callback_data="delete_last_file"),
        InlineKeyboardButton("↩️ Назад в меню", callback_data="back")
    )
    return kb

def is_throttled(chat_id: int, delay_sec: float = 1.5) -> bool:
    now = time.time()
    last_time = last_callback_time.get(chat_id, 0)
    if now - last_time < delay_sec:
        return True
    last_callback_time[chat_id] = now
    return False

@bot.message_handler(commands=['start', 'help'])
def cmd_start(message: Message):
    bot.send_message(
        message.chat.id,
        "Привет! Я бот для создания задач в Bitrix24.\nВыберите тип вопроса:",
        reply_markup=create_menu_keyboard()
    )

@bot.message_handler(func=lambda m: m.text in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"])
def handle_menu(message: Message):
    chat_id = message.chat.id
    user_state[chat_id] = {
        "choice": message.text,
        "buffer_text": "",
        "buffer_files": []
    }
    bot.send_message(
        chat_id,
        f"Вы выбрали: <b>{message.text}</b>\nПришлите текст или файлы.",
        reply_markup=create_finish_keyboard()
    )

@bot.message_handler(content_types=['text', 'photo', 'document', 'video'])
def collect_input(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_state:
        return

    state = user_state[chat_id]
    if message.content_type == 'text':
        state["buffer_text"] += message.text + "\n"
    else:
        link = file_link(message)
        if link:
            state["buffer_files"].append(link)

    preview = state["buffer_text"].strip() or "<i>(без текста)</i>"
    bot.send_message(
        chat_id,
        f"Черновик:\n{preview}\nФайлов: {len(state['buffer_files'])}",
        reply_markup=create_finish_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back", "delete_last_file", "cancel"])
def handle_callbacks(call: CallbackQuery):
    chat_id = call.message.chat.id
    data = call.data

    if is_throttled(chat_id):
        bot.answer_callback_query(call.id, "Подождите...")
        return

    if data == "back":
        user_state.pop(chat_id, None)
        bot.send_message(chat_id, "Меню:", reply_markup=create_menu_keyboard())

    elif data == "cancel":
        user_state.pop(chat_id, None)
        bot.send_message(chat_id, "Отменено.", reply_markup=create_menu_keyboard())

    elif data == "delete_last_file":
        if chat_id in user_state and user_state[chat_id]["buffer_files"]:
            user_state[chat_id]["buffer_files"].pop()
            bot.answer_callback_query(call.id, "Файл удалён")

    elif data == "ok":
        if chat_id not in user_state:
            bot.answer_callback_query(call.id, "Сессия устарела")
            return

        state = user_state[chat_id]
        if not state["buffer_text"] and not state["buffer_files"]:
            bot.answer_callback_query(call.id, "Добавьте текст или файлы")
            return

        # Загружаем файлы
        attached_ids = []
        for file_url in state["buffer_files"]:
            file_id = upload_file_to_bitrix(file_url)
            if file_id:
                attached_ids.append(file_id)
            else:
                logger.warning(f"Failed to upload file: {file_url}")

        # Создаём задачу
        success = create_bitrix_task(
            title=state["choice"],
            description=state["buffer_text"].strip(),
            responsible_id=RESPONSIBLE_IDS.get(state["choice"], CREATOR_ID),
            attached_ids=attached_ids or None
        )

        user_state.pop(chat_id, None)
        if success:
            bot.send_message(chat_id, "✅ Задача создана!", reply_markup=create_menu_keyboard())
        else:
            bot.send_message(chat_id, "❌ Ошибка создания задачи", reply_markup=create_menu_keyboard())

def run_bot():
    logger.info("Starting bot...")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.error(f"Bot error: {e}")
            time.sleep(5)

@app.route("/")
def index():
    return "Bot is running!"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))