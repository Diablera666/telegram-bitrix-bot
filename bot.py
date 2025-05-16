import os
from flask import Flask, request
import telebot
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ID сотрудников для задач
employee_ids = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('Вопрос 1', 'Вопрос 2', 'Вопрос 3', 'Другое')
    bot.send_message(message.chat.id, "Выберите пункт меню:", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text
    if text in employee_ids:
        employee_id = employee_ids[text]
        task_data = {
            "fields": {
                "TITLE": f"Задача из бота: {text}",
                "RESPONSIBLE_ID": employee_id,
                "DESCRIPTION": f"Задача создана из Telegram бота по пункту '{text}'"
            }
        }
        response = requests.post(BITRIX_WEBHOOK_URL, json=task_data)
        if response.status_code == 200:
            bot.send_message(message.chat.id, f"Задача по '{text}' создана!")
        else:
            bot.send_message(message.chat.id, f"Ошибка при создании задачи: {response.text}")
    else:
        bot.send_message(message.chat.id, "Пожалуйста, выберите пункт меню.")

@app.route(f"/{TOKEN}", methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

@app.route('/')
def index():
    return "Бот работает!"

if __name__ == "__main__":
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    webhook_url = f"{render_url}/{TOKEN}"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    port = int(os.environ.get('PORT', 10000))
    app.run(host="0.0.0.0", port=port)
