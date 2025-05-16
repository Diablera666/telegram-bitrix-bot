import os
import threading
from datetime import datetime, timedelta
from flask import Flask
import telebot
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- Функция для вычисления даты с учётом 3 рабочих дней ---
def add_workdays(start_date, workdays):
    current_date = start_date
    added_days = 0
    while added_days < workdays:
        current_date += timedelta(days=1)
        # Понедельник=0 ... Воскресенье=6
        if current_date.weekday() < 5:  # только рабочие дни
            added_days += 1
    return current_date

# --- Пример обработки команды и создания задачи ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Выберите пункт меню...")

# Здесь добавь логику выбора пункта меню и сбора текста/файлов (по твоему текущему сценарию)

def create_bitrix_task(task_title, task_description, responsible_id):
    deadline = add_workdays(datetime.now(), 3).strftime('%Y-%m-%dT%H:%M:%S')  # формат ISO

    data = {
        "fields": {
            "TITLE": task_title,
            "DESCRIPTION": task_description,
            "RESPONSIBLE_ID": responsible_id,
            "DEADLINE": deadline
        }
    }

    response = requests.post(BITRIX_WEBHOOK_URL, json=data)
    if response.status_code == 200:
        return True
    else:
        print("Ошибка создания задачи в Битрикс24:", response.text)
        return False

# Запуск polling в отдельном потоке
def run_bot():
    bot.infinity_polling()

@app.route("/")
def index():
    return "Bot is running!"

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
