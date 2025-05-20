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

def download_file(file_id):
    file_info = bot.get_file(file_id)
    file_path = file_info.file_path
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    local_filename = file_path.split('/')[-1]
    r = requests.get(url)
    with open(local_filename, 'wb') as f:
        f.write(r.content)
    return local_filename

def upload_to_bitrix_disk(file_path):
    url = BITRIX_WEBHOOK_URL.replace('task.item.add', 'disk.folder.uploadfile')
    folder_id = 1  # корневая папка (можно задать другую)

    with open(file_path, 'rb') as f:
        files = {'file': (file_path, f)}
        data = {'id': folder_id, 'generateUniqueName': 'Y'}
        response = requests.post(url, data=data, files=files)

    if response.status_code == 200 and 'result' in response.json():
        return response.json()['result']['ID']
    print("Upload error:", response.text)
    return None

def create_bitrix_task(title: str, description: str, responsible_id: int, file_ids=None) -> bool:
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')
    fields = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": responsible_id,
        "CREATED_BY": CREATOR_ID,
        "DEADLINE": deadline
    }
    if file_ids:
        fields["UF_TASK_WEBDAV_FILES"] = file_ids

    payload = {"fields": fields}
    resp = requests.post(BITRIX_WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code == 200:
        return True
    print("Bitrix24 error:", resp.text)
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
    return kb

# ----------- состояния пользователей ----------
user_state = {}  # chat_id → {choice, buffer_text, file_ids}

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
                        "file_ids": []}
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
        try:
            file_id = None
            if message.content_type == "photo":
                file_id = message.photo[-1].file_id
            elif message.content_type == "video":
                file_id = message.video.file_id
            elif message.content_type == "document":
                file_id = message.document.file_id

            if file_id:
                local_file = download_file(file_id)
                file_disk_id = upload_to_bitrix_disk(local_file)
                if file_disk_id:
                    st["file_ids"].append(file_disk_id)
                os.remove(local_file)
        except Exception as e:
            print("File processing error:", e)

    preview = st["buffer_text"].strip() or "(без текста)"
    bot.send_message(chat,
                     f"Черновик:\n{preview}\n\nФайлов: {len(st['file_ids'])}",
                     reply_markup=finish_kb())

@bot.callback_query_handler(func=lambda c: c.data in ["ok", "back"])
def inline_buttons(call):
    chat = call.message.chat.id
    data = call.data

    if data == "back":
        user_state.pop(chat, None)
        bot.edit_message_reply_markup(chat, call.message.message_id,
                                      reply_markup=None)
        bot.send_message(chat, "Возврат в меню.", reply_markup=menu_kb)

    elif data == "ok":
        st = user_state.pop(chat, None)
        if not st:
            return

        author = (f"@{call.from_user.username}"
                  if call.from_user.username
                  else f"{call.from_user.first_name or ''} "
                       f"{call.from_user.last_name or ''}".strip())

        description = f"Автор: {author}\n\n{st['buffer_text'].strip()}"
        resp_id = 270 if st["choice"] in ["Вопрос 1", "Вопрос 3"] else 12
        success = create_bitrix_task(st["choice"], description, resp_id, st["file_ids"])

        bot.edit_message_reply_markup(chat, call.message.message_id,
                                      reply_markup=None)
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
