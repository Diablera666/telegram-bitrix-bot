import os
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

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ---------------------- вспом-функции ----------------------
def add_workdays(start_date: datetime, workdays: int) -> datetime:
    cur = start_date
    added = 0
    while added < workdays:
        cur += timedelta(days=1)
        if cur.weekday() < 5:          # Пн-Пт
            added += 1
    return cur

def file_link(message) -> str:
    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
    elif message.content_type == "video":
        file_id = message.video.file_id
    elif message.content_type == "document":
        file_id = message.document.file_id
    else:
        return ""
    info = bot.get_file(file_id)
    return f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"

def create_bitrix_task(title: str, description: str, responsible_id: int) -> bool:
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')
    payload = {
        "fields": {
            "TITLE": title,
            "DESCRIPTION": description,
            "RESPONSIBLE_ID": responsible_id,
            "DEADLINE": deadline
        }
    }
    resp = requests.post(BITRIX_WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code == 200:
        return True
    print("Bitrix24 error:", resp.text)
    return False

# ---------------------- клавиатуры ----------------------
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

# ---------------------- состояния ----------------------
user_state = {}          # chat_id → {choice, buffer_text, buffer_files}

# ---------------------- хэндлеры ----------------------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "Привет! Выберите пункт меню:",
        reply_markup=menu_kb
    )

@bot.message_handler(func=lambda m: m.text in ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"])
def handle_menu(message):
    chat = message.chat.id
    user_state[chat] = {"choice": message.text, "buffer_text": "", "buffer_files": []}
    bot.send_message(
        chat,
        f"Вы выбрали: <b>{message.text}</b>\n"
        "Пришлите текст или файлы. Когда закончите — нажмите «✅ Подтвердить».",
        reply_markup=finish_kb()
    )

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
    bot.send_message(
        chat,
        f"Черновик:\n{preview}\n\nФайлов: {len(st['buffer_files'])}",
        reply_markup=finish_kb()
    )

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back"])
def inline_buttons(call):
    chat = call.message.chat.id
    data = call.data

    if data == "back":
        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)

    elif data == "ok":
        st = user_state.pop(chat, None)
        if not st:
            return
        description = st["buffer_text"].strip()
        if st["buffer_files"]:
            description += "\n\nСсылки на файлы:\n" + "\n".join(st["buffer_files"])
        resp_id = 270 if st["choice"] in ["Вопрос 1", "Вопрос 3"] else 12
        success = create_bitrix_task(st["choice"], description, resp_id)

        bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=None)
        bot.send_message(
            chat,
            "✅ Задача создана!" if success else "❌ Не удалось создать задачу.",
            reply_markup=menu_kb
        )

# ---------------------- запуск ----------------------
def run_bot():
    bot.delete_webhook()
    bot.infinity_polling()

@app.route("/")
def index():
    return "Bot is running!"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
