import os
import time
import threading
import logging
from http.client import RemoteDisconnected
from datetime import datetime, timedelta

from flask import Flask
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
import requests
from dotenv import load_dotenv

# ──────────────────────────── настройка ───────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN              = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")      # …/task.item.add.json
CREATOR_ID         = 12                                   # постановщик
FILE_FIELD         = "UF_TASK_WEBDAV_FILES"               # ← вернули старое поле

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ─────────────────────── вспом-функции ───────────────────────────
def add_workdays(start_date: datetime, workdays: int) -> datetime:
    cur, added = start_date, 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def file_link(message) -> str:
    file_id = (
        message.photo[-1].file_id if message.content_type == "photo" else
        message.video.file_id     if message.content_type == "video" else
        message.document.file_id  if message.content_type == "document" else
        None
    )
    if not file_id:
        return ""
    info = bot.get_file(file_id)
    return f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"


def attach_from_url(file_url: str) -> str | None:
    disk_url = BITRIX_WEBHOOK_URL.replace("task.item.add", "disk.attachedObject.add")
    try:
        resp = requests.post(disk_url,
                             json={"fields": {"FILE_CONTENT_URL": file_url}},
                             timeout=60)
        data = resp.json()
        if "error" in data:
            logging.warning("Disk attach error for %s: %s", file_url, data)
            return None
        return data["result"]["ID"]
    except Exception as e:
        logging.warning("Attach failed for %s: %s", file_url, e)
        return None


def create_bitrix_task(title: str, description: str,
                       responsible_id: int, file_urls: list[str]) -> bool:
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')
    attached_ids = [fid for url in file_urls if (fid := attach_from_url(url))]

    fields = {
        "TITLE":          title,
        "DESCRIPTION":    description,
        "RESPONSIBLE_ID": responsible_id,
        "CREATED_BY":     CREATOR_ID,
        "DEADLINE":       deadline
    }
    if attached_ids:
        fields[FILE_FIELD] = attached_ids        # ← снова UF_TASK_WEBDAV_FILES

    try:
        resp = requests.post(BITRIX_WEBHOOK_URL,
                             json={"fields": fields},
                             timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logging.exception("Bitrix24 task add error: %s", e)
        return False


# ───────────────── декоратор безопасности ─────────────────────────
def safe_handler(func):
    def wrapper(message_or_call):
        try:
            return func(message_or_call)
        except Exception:
            logging.exception("Handler error:")
    return wrapper

# ───────────────────────── клавиатуры ─────────────────────────────
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
    return kb

# ─────────────────── состояния пользователей ─────────────────────
user_state: dict[int, dict] = {}

# ────────────────────────── хэндлеры ──────────────────────────────
@bot.message_handler(commands=['start'])
@safe_handler
def cmd_start(message):
    bot.send_message(message.chat.id,
                     "Привет! Выберите пункт меню:",
                     reply_markup=menu_kb)

@bot.message_handler(func=lambda m: m.text in
                     ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"])
@safe_handler
def handle_menu(message):
    chat = message.chat.id
    user_state[chat] = {"choice": message.text,
                        "buffer_text": "",
                        "buffer_files": []}
    bot.send_message(chat,
                     f"Вы выбрали: <b>{message.text}</b>\n"
                     "Пришлите текст или файлы. Когда закончите — нажмите «✅ Подтвердить».",
                     reply_markup=finish_kb())

@bot.message_handler(content_types=['text', 'photo', 'document', 'video'])
@safe_handler
def collect_input(message):
    chat = message.chat.id
    if chat not in user_state:
        return
    st = user_state[chat]

    if message.content_type == 'text':
        st["buffer_text"] += message.text + "\n"
    else:
        if (link := file_link(message)):
            st["buffer_files"].append(link)

    preview = st["buffer_text"].strip() or "(без текста)"
    bot.send_message(chat,
                     f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}",
                     reply_markup=finish_kb())

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back"])
@safe_handler
def inline_buttons(call):
    chat = call.message.chat.id

    if call.data == "back":
        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)
        return

    st = user_state.pop(chat, None)
    if not st:
        return

    author = (f"@{call.from_user.username}"
              if call.from_user.username else
              f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip())
    description = f"Автор: {author}\n\n{st['buffer_text'].strip()}"

    responsible_id = 270 if st["choice"] in ["Вопрос 1", "Вопрос 3"] else 12
    success = create_bitrix_task(st["choice"], description, responsible_id, st["buffer_files"])

    bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
    bot.send_message(chat,
                     "✅ Задача создана!" if success else "❌ Не удалось создать задачу.",
                     reply_markup=menu_kb)

# ────────────────────────── запуск ────────────────────────────────
def run_bot():
    # Явно удаляем webhook перед polling
    bot.remove_webhook()
    while True:
        try:
            bot.infinity_polling(long_polling_timeout=25, timeout=10, skip_pending=True)
        except (RemoteDisconnected,
                requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout):
            logging.warning("Polling connection lost, retrying in 5 s…")
            time.sleep(5)
        except Exception:
            logging.exception("Fatal polling error, restarting in 5 s")
            time.sleep(5)

@app.route("/")
def index():
    return "Bot is running!"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
