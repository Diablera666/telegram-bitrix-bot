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
STORAGE_ID = 11
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
last_callback_time: Dict[int, float] = {}  # Для защиты от частых нажатий

# ---------------- вспомогательные функции ----------------
def add_workdays(start_date: datetime, workdays: int) -> datetime:
    """Добавляет указанное количество рабочих дней к дате."""
    cur = start_date
    added = 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # Пн-Пт
            added += 1
    return cur

def get_file_info(message: Message) -> Optional[Dict[str, str]]:
    """Получает информацию о файле из сообщения."""
    content_type = message.content_type
    try:
        if content_type == "photo":
            return {"file_id": message.photo[-1].file_id, "type": "photo"}
        elif content_type == "video":
            return {"file_id": message.video.file_id, "type": "video"}
        elif content_type == "document":
            return {"file_id": message.document.file_id, "type": "document"}
        else:
            logger.warning(f"Unsupported content type: {content_type}")
            return None
    except Exception as e:
        logger.error(f"Error getting file info: {e}")
        return None

def file_link(message: Message) -> Optional[str]:
    """Получает ссылку на файл Telegram для предварительного просмотра."""
    try:
        file_info = get_file_info(message)
        if not file_info:
            return None

        info = bot.get_file(file_info["file_id"])
        return f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"

    except Exception as e:
        logger.error(f"Error getting file link: {e}")
        return None

def download_file(url: str) -> Optional[str]:
    """Скачивает файл по URL во временный файл."""
    try:
        local_filename = url.split('/')[-1].split('?')[0]
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_filename
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return None

def upload_file_to_bitrix(file_url: str, folder_id: int = PARENT_ID) -> Optional[int]:
    """
    Загружает файл из URL в Bitrix24 в указанную папку.
    Возвращает attachedId или None в случае ошибки.
    """
    local_filename = None
    try:
        local_filename = download_file(file_url)
        if not local_filename:
            return None

        upload_url = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.folder.uploadfile.json')
        
        with open(local_filename, 'rb') as f:
            files = {'file': f}
            data = {'id': folder_id}
            response = requests.post(upload_url, data=data, files=files, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if 'result' in result:
                return result['result']['attachedId']
            
            logger.error(f"Bitrix24 upload error: {result.get('error', 'Unknown error')}")
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
    """
    Создает задачу в Bitrix24 с указанными параметрами.
    Возвращает True в случае успеха, False в случае ошибки.
    """
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')
    fields = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": responsible_id,
        "CREATED_BY": CREATOR_ID,
        "DEADLINE": deadline
    }
    
    if attached_ids:
        fields["UF_TASK_WEBDAV_FILES"] = attached_ids

    try:
        response = requests.post(
            BITRIX_WEBHOOK_URL,
            json={"fields": fields},
            timeout=15
        )
        response.raise_for_status()
        
        if response.status_code == 200:
            return True
            
        logger.error(f"Bitrix24 task creation error: {response.text}")
        return False
        
    except Exception as e:
        logger.error(f"Error creating Bitrix task: {e}")
        return False

# ---------------- клавиатуры ----------------
def create_menu_keyboard() -> ReplyKeyboardMarkup:
    """Создает основную клавиатуру меню."""
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("Вопрос 1"),
        KeyboardButton("Вопрос 2"),
        KeyboardButton("Вопрос 3"),
        KeyboardButton("Другое")
    )
    return kb

def create_finish_keyboard() -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру для завершения работы с задачей."""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data="ok"),
        InlineKeyboardButton("❌ Отменить", callback_data="cancel"),
        InlineKeyboardButton("🗑️ Удалить последний файл", callback_data="delete_last_file"),
        InlineKeyboardButton("↩️ Назад в меню", callback_data="back")
    )
    return kb

# ----------- обработка состояний ----------
def is_throttled(chat_id: int, delay_sec: float = 1.5) -> bool:
    """Проверяет, не слишком ли часто приходят запросы от пользователя."""
    now = time.time()
    last_time = last_callback_time.get(chat_id, 0)
    
    if now - last_time < delay_sec:
        return True
        
    last_callback_time[chat_id] = now
    return False

def clear_user_state(chat_id: int) -> None:
    """Очищает состояние пользователя."""
    if chat_id in user_state:
        del user_state[chat_id]

def get_user_info(call: CallbackQuery) -> str:
    """Формирует строку с информацией о пользователе."""
    user = call.from_user
    if user.username:
        return f"@{user.username}"
    return f"{user.first_name or ''} {user.last_name or ''}".strip()

# --------------- хэндлеры ----------------
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message: Message) -> None:
    """Обработчик команд /start и /help."""
    bot.send_message(
        message.chat.id,
        "Привет! Я бот для создания задач в Bitrix24.\n"
        "Выберите тип вопроса из меню ниже:",
        reply_markup=create_menu_keyboard()
    )

@bot.message_handler(func=lambda m: m.text in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"])
def handle_menu(message: Message) -> None:
    """Обработчик выбора пункта меню."""
    chat_id = message.chat.id
    choice = message.text
    
    user_state[chat_id] = {
        "choice": choice,
        "buffer_text": "",
        "buffer_files": []
    }
    
    bot.send_message(
        chat_id,
        f"Вы выбрали: <b>{choice}</b>\n\n"
        "Пришлите текст сообщения или файлы (фото, видео, документы).\n"
        "Когда закончите, нажмите «✅ Подтвердить».",
        reply_markup=create_finish_keyboard()
    )

@bot.message_handler(content_types=['text', 'photo', 'document', 'video'])
def collect_input(message: Message) -> None:
    """Собирает ввод пользователя (текст и файлы)."""
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
        else:
            logger.warning(f"Failed to get file link for {message.content_type}")

    # Формируем превью сообщения
    preview = state["buffer_text"].strip() or "<i>(текст не добавлен)</i>"
    files_count = len(state["buffer_files"])
    
    bot.send_message(
        chat_id,
        f"<b>Текущий черновик:</b>\n{preview}\n\n"
        f"<b>Прикреплено файлов:</b> {files_count}",
        reply_markup=create_finish_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back", "delete_last_file", "cancel"])
def handle_callbacks(call: CallbackQuery) -> None:
    """Обработчик inline-кнопок."""
    chat_id = call.message.chat.id
    data = call.data

    # Защита от частых нажатий
    if is_throttled(chat_id):
        bot.answer_callback_query(call.id, "Пожалуйста, подождите...")
        return

    if data == "back":
        clear_user_state(chat_id)
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        bot.send_message(
            chat_id,
            "Возвращаемся в главное меню.",
            reply_markup=create_menu_keyboard()
        )
        return

    if data == "cancel":
        clear_user_state(chat_id)
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        bot.send_message(
            chat_id,
            "Создание задачи отменено.",
            reply_markup=create_menu_keyboard()
        )
        return

    state = user_state.get(chat_id)
    if not state:
        bot.answer_callback_query(call.id, "Сессия устарела. Пожалуйста, выберите пункт меню заново.")
        bot.send_message(chat_id, "Возврат в меню.", reply_markup=create_menu_keyboard())
        return

    if data == "delete_last_file":
        if not state["buffer_files"]:
            bot.answer_callback_query(call.id, "Нет файлов для удаления.")
            return

        state["buffer_files"].pop()
        bot.answer_callback_query(call.id, "Последний файл удалён.")

        preview = state["buffer_text"].strip() or "<i>(текст не добавлен)</i>"
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"<b>Текущий черновик:</b>\n{preview}\n\n"
                 f"<b>Прикреплено файлов:</b> {len(state['buffer_files'])}",
            reply_markup=create_finish_keyboard()
        )
        return

    if data == "ok":
        # Проверяем, что есть хотя бы текст или файлы
        if not state["buffer_text"].strip() and not state["buffer_files"]:
            bot.answer_callback_query(
                call.id,
                "Добавьте текст или файлы перед подтверждением!"
            )
            return

        author = get_user_info(call)
        description = f"Автор: {author}\n\n{state['buffer_text'].strip()}"

        # Загружаем файлы в Bitrix24
        attached_ids = []
        for file_url in state["buffer_files"]:
            attached_id = upload_file_to_bitrix(file_url)
            if attached_id:
                attached_ids.append(attached_id)
            else:
                logger.warning(f"Failed to upload file: {file_url}")

        # Получаем ID ответственного
        responsible_id = RESPONSIBLE_IDS.get(state["choice"], CREATOR_ID)

        # Создаем задачу
        success = create_bitrix_task(
            title=state["choice"],
            description=description,
            responsible_id=responsible_id,
            attached_ids=attached_ids or None
        )

        # Очищаем состояние пользователя
        clear_user_state(chat_id)

        # Отправляем результат пользователю
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        
        if success:
            bot.send_message(
                chat_id,
                "✅ Задача успешно создана в Bitrix24!",
                reply_markup=create_menu_keyboard()
            )
        else:
            bot.send_message(
                chat_id,
                "❌ Не удалось создать задачу. Пожалуйста, попробуйте позже.",
                reply_markup=create_menu_keyboard()
            )

# --------------- запуск ----------------
def run_bot() -> None:
    """Запускает бота в режиме polling."""
    logger.info("Starting bot...")
    bot.delete_webhook()

    while True:
        try:
            bot.infinity_polling(
                long_polling_timeout=25,
                timeout=10,
                skip_pending=True
            )
        except Exception as e:
            logger.error(f"Polling crashed: {e}")
            time.sleep(5)

@app.route("/")
def index() -> str:
    """Обработчик для веб-сервера Flask."""
    return "Telegram bot is running!"

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Запускаем Flask-сервер
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)