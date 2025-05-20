import os
import time
import threading
import tempfile
from datetime import datetime, timedelta
from flask import Flask
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
BITRIX_DISK_UPLOAD_URL = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.folder.uploadfile.json')
BITRIX_ATTACH_OBJECT_URL = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.attachedobject.add.json')
CREATOR_ID = 12  # ID постановщика задачи в Битрикс24

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ максимум

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ---------------- вспомогательные функции ----------------

def add_workdays(start_date: datetime, workdays: int) -> datetime:
    cur = start_date
    added = 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # Пн-Пт
            added += 1
    return cur

def download_telegram_file(file_id: str) -> str:
    """
    Скачивает файл из Telegram во временную папку.
    Возвращает путь к файлу или пустую строку при ошибке.
    """
    try:
        file_info = bot.get_file(file_id)
        file_path = file_info.file_path
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

        resp = requests.get(file_url, stream=True)
        if resp.status_code != 200:
            print(f"[download_telegram_file] Ошибка скачивания файла {file_id}")
            return ""

        suffix = os.path.splitext(file_path)[1]
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        for chunk in resp.iter_content(chunk_size=8192):
            tmp_file.write(chunk)
        tmp_file.close()

        # Проверка размера файла
        if os.path.getsize(tmp_file.name) > MAX_FILE_SIZE:
            print(f"[download_telegram_file] Файл слишком большой: {tmp_file.name}")
            os.unlink(tmp_file.name)
            return ""

        return tmp_file.name
    except Exception as e:
        print(f"[download_telegram_file] Ошибка: {e}")
        return ""

def upload_file_to_bitrix(file_path: str) -> str:
    """
    Загружает файл на диск Битрикс24, возвращает ID загруженного файла.
    """
    try:
        with open(file_path, 'rb') as f:
            # Загрузка файла в корень диска (folder_id=0)
            resp = requests.post(BITRIX_DISK_UPLOAD_URL,
                                 data={"id": 0},
                                 files={"file": f},
                                 timeout=30)
            data = resp.json()
            if resp.status_code == 200 and data.get("result") and data["result"].get("FILE_ID"):
                return data["result"]["FILE_ID"]
            print(f"[upload_file_to_bitrix] Ошибка загрузки файла: {data}")
    except Exception as e:
        print(f"[upload_file_to_bitrix] Исключение: {e}")
    return ""

def attach_file_to_task(task_id: int, file_id: int) -> bool:
    """
    Прикрепляет файл из диска Битрикс24 к задаче.
    """
    try:
        payload = {
            "attachedObject": {
                "TASK_ID": task_id,
                "ATTACHED_OBJECT_TYPE": "FILE",
                "ATTACHED_OBJECT_ID": file_id
            }
        }
        resp = requests.post(BITRIX_ATTACH_OBJECT_URL, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("result"):
            return True
        print(f"[attach_file_to_task] Ошибка прикрепления: {resp.text}")
    except Exception as e:
        print(f"[attach_file_to_task] Исключение: {e}")
    return False

def create_bitrix_task(title: str, description: str, responsible_id: int, file_ids=None) -> bool:
    """
    Создаёт задачу и прикрепляет файлы, если file_ids переданы.
    """
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')
    payload = {
        "fields": {
            "TITLE": title,
            "DESCRIPTION": description,
            "RESPONSIBLE_ID": responsible_id,
            "CREATED_BY": CREATOR_ID,
            "DEADLINE": deadline
        }
    }
    try:
        resp = requests.post(BITRIX_WEBHOOK_URL, json=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("result") and data["result"].get("TASK_ID"):
            task_id = data["result"]["TASK_ID"]

            # Прикрепляем файлы к задаче
            if file_ids:
                for fid in file_ids:
                    attach_file_to_task(task_id, fid)

            return True
        print("Bitrix24 error:", resp.text)
    except Exception as e:
        print("Bitrix24 request error:", e)
    return False

# ---------------- клавиатуры ----------------

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add(
    KeyboardButton("Вопрос 1"),
    KeyboardButton("Вопрос 2"),
    KeyboardButton("Вопрос 3"),
    KeyboardButton("Другое")
)

def finish_kb(has_files: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Подтвердить", callback_data="ok"))
    kb.add(InlineKeyboardButton("↩️ Назад в меню", callback_data="back"))
    if has_files:
        kb.add(InlineKeyboardButton("❌ Удалить последний файл", callback_data="del_last"))
    return kb

# ----------- состояния пользователей ----------

user_state = {}  # chat_id → {choice, buffer_text, buffer_files (list of local file paths)}

# --------------- хэндлеры ----------------

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id,
                     "Привет! Выберите пункт меню:",
                     reply_markup=menu_kb)

@bot.message_handler(func=lambda m: m.text in
                     ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"])
def handle_menu(message):
    chat = message.chat.id
    # Если остались файлы из предыдущего сеанса, удалим их с диска
    prev = user_state.get(chat)
    if prev and prev.get("buffer_files"):
        for f in prev["buffer_files"]:
            if os.path.exists(f):
                os.unlink(f)

    user_state[chat] = {"choice": message.text,
                        "buffer_text": "",
                        "buffer_files": []}
    bot.send_message(chat,
                     f"Вы выбрали: <b>{message.text}</b>\n"
                     "Пришлите текст или файлы. Когда закончите — нажмите «✅ Подтвердить».",
                     reply_markup=finish_kb())

@bot.message_handler(content_types=['text', 'photo', 'document', 'video'])
def collect_input(message):
    chat = message.chat.id
    if chat not in user_state:
        return

    st = user_state[chat]

    if message.content_type == 'text':
        st["buffer_text"] += message.text + "\n"
    else:
        # Получаем file_id для скачивания
        file_id = None
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
        elif message.content_type == "video":
            file_id = message.video.file_id
        elif message.content_type == "document":
            file_id = message.document.file_id
        else:
            bot.send_message(chat, "Тип файла не поддерживается.")
            return

        file_path = download_telegram_file(file_id)
        if file_path:
            st["buffer_files"].append(file_path)
            bot.send_message(chat, f"Файл принят ({os.path.basename(file_path)}).")
        else:
            bot.send_message(chat, "Не удалось скачать или файл слишком большой (максимум 10 МБ).")

    preview = st["buffer_text"].strip() or "(без текста)"
    bot.send_message(chat,
                     f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}",
                     reply_markup=finish_kb(has_files=bool(st["buffer_files"])))

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back", "del_last"])
def inline_buttons(call):
    chat = call.message.chat.id
    data = call.data

    if chat not in user_state:
        bot.answer_callback_query(call.id, "Сессия истекла, начните заново.")
        return

    st = user_state[chat]

    if data == "back":
        # Удаляем временные файлы
        for f in st.get("buffer_files", []):
            if os.path.exists(f):
                os.unlink(f)
        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)

    elif data == "del_last":
        if st["buffer_files"]:
            last_file = st["buffer_files"].pop()
            if os.path.exists(last_file):
                os.unlink(last_file)
            bot.answer_callback_query(call.id, "Последний файл удалён.")
            preview = st["buffer_text"].strip() or "(без текста)"
            bot.edit_message_text(chat,
                                  call.message.message_id,
                                  f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}",
                                  reply_markup=finish_kb(has_files=bool(st["buffer_files"])))
        else:
            bot.answer_callback_query(call.id, "Нет файлов для удаления.")

    elif data == "ok":
        author = (f"@{call.from_user.username}"
                  if call.from_user.username
                  else f"{call.from_user.first_name or ''} "
                       f"{call.from_user.last_name or ''}".strip())

        description = f"Автор: {author}\n\n{st['buffer_text'].strip()}"

        # Загружаем файлы на Битрикс диск и собираем ID
        file_ids = []
        for fpath in st["buffer_files"]:
            fid = upload_file_to_bitrix(fpath)
            if fid:
                file_ids.append(fid)
            # Удаляем временный файл после загрузки
            if os.path.exists(fpath):
                os.unlink(fpath)

        resp_id = 270 if st["choice"] in ["Вопрос 1", "Вопрос 3"] else 12
        success = create_bitrix_task(st["choice"], description, resp_id, file_ids=file_ids)

        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat,
                         "✅ Задача создана!" if success
                         else "❌ Не удалось создать задачу.",
                         reply_markup=menu_kb)

# --------------- запуск ----------------

def run_bot():
    bot.delete_webhook()
    while True:
        try:
            bot.infinity_polling(
                long_polling_timeout=25,
                timeout=10,
                skip_pending=True
            )
        except Exception as e:
            print("Polling crashed:", e)
            time.sleep(5)

@app.route("/")
def index():
    return "Bot is running!"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
