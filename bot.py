import os
import time
from datetime import datetime, timedelta
from flask import Flask, request
from dotenv import load_dotenv
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, Update
)
import requests

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например: https://yourapp.onrender.com/webhook

CREATOR_ID = 12  # ID постановщика задачи в Bitrix24
STORAGE_ID = 11
PARENT_ID = 5636  # Папка "Загруженные файлы" на общем диске Bitrix24

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ========== Вспомогательные функции ==========

def add_workdays(start_date: datetime, workdays: int) -> datetime:
    cur = start_date
    added = 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur

def file_link(message) -> str:
    try:
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
        elif message.content_type == "video":
            file_id = message.video.file_id
        elif message.content_type == "document":
            file_id = message.document.file_id
        else:
            print(f"[file_link] Unsupported content type: {message.content_type}")
            return ""

        info = bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"

    except Exception as e:
        print(f"[file_link] Error: {e}")
        return ""

def upload_file_to_bitrix(file_url: str, folder_id=PARENT_ID) -> int:
    try:
        local_filename = file_url.split('/')[-1].split('?')[0]
        resp = requests.get(file_url, stream=True, timeout=30)
        resp.raise_for_status()

        with open(local_filename, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        with open(local_filename, 'rb') as f:
            files = {'file': f}
            data = {'id': folder_id}
            upload_url = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.folder.uploadfile.json')
            response = requests.post(upload_url, data=data, files=files, timeout=30)
            response.raise_for_status()
            result = response.json()
            attached_id = result.get('result', {}).get('attachedId')

        os.remove(local_filename)
        return attached_id

    except Exception as e:
        print(f"[upload_file_to_bitrix] Ошибка загрузки файла: {e}")
        try:
            os.remove(local_filename)
        except Exception:
            pass
        return None

def create_bitrix_task(title: str, description: str, responsible_id: int, attached_ids=None) -> bool:
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

    payload = {"fields": fields}

    try:
        resp = requests.post(BITRIX_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code == 200:
            return True
        print("Bitrix24 error:", resp.text)
    except Exception as e:
        print("Bitrix24 request error:", e)
    return False

# ========== Клавиатуры ==========

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add(
    KeyboardButton("Вопрос 1"),
    KeyboardButton("Вопрос 2"),
    KeyboardButton("Вопрос 3"),
    KeyboardButton("Другое")
)

def finish_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Подтвердить", callback_data="ok"))
    kb.add(InlineKeyboardButton("↩️ Назад в меню", callback_data="back"))
    kb.add(InlineKeyboardButton("🗑️ Удалить последний файл", callback_data="delete_last_file"))
    return kb

# ========== Состояния пользователей ==========

user_state = {}  # chat_id → {choice, buffer_text, buffer_files}
last_callback_time = {}

def is_throttled(chat_id: int, delay_sec: float = 1.5) -> bool:
    now = time.time()
    last = last_callback_time.get(chat_id, 0)
    if now - last < delay_sec:
        return True
    last_callback_time[chat_id] = now
    return False

# ========== Обработчики ==========

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id, "Привет! Выберите пункт меню:", reply_markup=menu_kb)

@bot.message_handler(func=lambda m: m.text in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"])
def handle_menu(message):
    chat = message.chat.id
    user_state[chat] = {"choice": message.text, "buffer_text": "", "buffer_files": []}
    bot.send_message(chat, f"Вы выбрали: <b>{message.text}</b>\nПришлите текст или файлы. Когда закончите — нажмите «✅ Подтвердить».", reply_markup=finish_kb())

@bot.message_handler(content_types=['text', 'photo', 'document', 'video'])
def collect_input(message):
    chat = message.chat.id
    if chat not in user_state:
        return

    st = user_state[chat]
    if message.content_type == 'text':
        st["buffer_text"] += message.text + "\n"
    else:
        link = file_link(message)
        if link:
            st["buffer_files"].append(link)

    preview = st["buffer_text"].strip() or "(без текста)"
    bot.send_message(chat, f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}", reply_markup=finish_kb())

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back", "delete_last_file"])
def inline_buttons(call):
    chat = call.message.chat.id
    data = call.data

    if is_throttled(chat):
        bot.answer_callback_query(call.id, "Пожалуйста, не спешите.")
        return

    if data == "back":
        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)

    elif data == "delete_last_file":
        st = user_state.get(chat)
        if not st or not st["buffer_files"]:
            bot.answer_callback_query(call.id, "Файлов для удаления нет.")
            return
        st["buffer_files"].pop()
        bot.answer_callback_query(call.id, "Последний файл удалён.")

        preview = st["buffer_text"].strip() or "(без текста)"
        bot.edit_message_text(chat_id=chat, message_id=call.message.message_id, text=f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}", reply_markup=finish_kb())

    elif data == "ok":
        st = user_state.pop(chat, None)
        if not st:
            bot.answer_callback_query(call.id, "Сессия не найдена.")
            return

        author = f"@{call.from_user.username}" if call.from_user.username else f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip()
        description = f"Автор: {author}\n\n{st['buffer_text'].strip()}"

        attached_ids = []
        for file_url in st["buffer_files"]:
            attached_id = upload_file_to_bitrix(file_url)
            if attached_id:
                attached_ids.append(attached_id)

        resp_id = 270 if st["choice"] in ["Вопрос 1", "Вопрос 3"] else 12
        success = create_bitrix_task(st["choice"], description, resp_id, attached_ids)

        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat, "✅ Задача создана!" if success else "❌ Не удалось создать задачу.", reply_markup=menu_kb)

# ========== Flask: обработка webhook ==========

@app.route("/")
def index():
    return "Бот работает через webhook!"

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    return "", 403

# ========== Установка webhook и запуск ==========

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
