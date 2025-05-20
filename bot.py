import os
import time
import threading
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
CREATOR_ID = 12  # ID постановщика задачи в Битрикс24

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ---------------- вспом-функции ----------------
def add_workdays(start_date: datetime, workdays: int) -> datetime:
    cur = start_date
    added = 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # Пн-Пт
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

        print(f"[file_link] file_id = {file_id}")
        info = bot.get_file(file_id)
        print(f"[file_link] file_path = {info.file_path}")
        return f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"

    except Exception as e:
        print(f"[file_link] Error: {e}")
        return ""

def create_bitrix_task(title: str, description: str, responsible_id: int) -> bool:
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
        if resp.status_code == 200:
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

def finish_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Подтвердить", callback_data="ok"))
    kb.add(InlineKeyboardButton("↩️ Назад в меню", callback_data="back"))
    kb.add(InlineKeyboardButton("🗑️ Удалить последний файл", callback_data="delete_last_file"))
    return kb

# ----------- состояния пользователей ----------
user_state = {}  # chat_id → {choice, buffer_text, buffer_files}
last_callback_time = {}  # chat_id → timestamp последней обработки callback (защита от частых нажатий)

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
        link = file_link(message)
        if link:
            st["buffer_files"].append(link)
        else:
            print(f"[collect_input] Не удалось получить ссылку на файл от {message.content_type}")

    preview = st["buffer_text"].strip() or "(без текста)"
    bot.send_message(chat,
                     f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}",
                     reply_markup=finish_kb())

def is_throttled(chat_id: int, delay_sec: float = 1.5) -> bool:
    """Проверка на слишком частые callback-запросы."""
    now = time.time()
    last = last_callback_time.get(chat_id, 0)
    if now - last < delay_sec:
        return True
    last_callback_time[chat_id] = now
    return False

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back", "delete_last_file"])
def inline_buttons(call):
    chat = call.message.chat.id
    data = call.data

    # Защита от частых нажатий
    if is_throttled(chat):
        bot.answer_callback_query(call.id, "Пожалуйста, не спешите.")
        return

    if data == "back":
        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)

    elif data == "delete_last_file":
        st = user_state.get(chat)
        if not st:
            bot.answer_callback_query(call.id, "Сессия не найдена. Пожалуйста, выберите пункт меню заново.")
            bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)
            return

        if not st["buffer_files"]:
            bot.answer_callback_query(call.id, "Файлов для удаления нет.")
            return

        st["buffer_files"].pop()
        bot.answer_callback_query(call.id, "Последний файл удалён.")

        preview = st["buffer_text"].strip() or "(без текста)"
        bot.edit_message_text(
            chat_id=chat,
            message_id=call.message.message_id,
            text=f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}",
            reply_markup=finish_kb()
        )

    elif data == "ok":
        st = user_state.pop(chat, None)
        if not st:
            bot.answer_callback_query(call.id, "Сессия не найдена. Пожалуйста, выберите пункт меню заново.")
            bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)
            return

        author = (f"@{call.from_user.username}"
                  if call.from_user.username
                  else f"{call.from_user.first_name or ''} "
                       f"{call.from_user.last_name or ''}".strip())

        description = f"Автор: {author}\n\n{st['buffer_text'].strip()}"
        if st["buffer_files"]:
            description += ("\n\nСсылки на файлы:\n" +
                            "\n".join(st["buffer_files"]))

        resp_id = 270 if st["choice"] in ["Вопрос 1", "Вопрос 3"] else 12
        success = create_bitrix_task(st["choice"], description, resp_id)

        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat,
                         "✅ Задача создана!" if success else "❌ Не удалось создать задачу.",
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
