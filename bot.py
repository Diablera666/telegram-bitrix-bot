import os
import telebot
import requests

# Токен и вебхук
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK_URL")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# user_state будет хранить состояние по каждому пользователю
user_state = {}

# ID сотрудников в Bitrix24 по выбранному пункту
responsible_ids = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}


@bot.message_handler(commands=['start'])
def start(message):
    user_state[message.chat.id] = {}
    show_main_menu(message.chat.id)


def show_main_menu(chat_id):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    buttons = ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]
    markup.add(*buttons)
    bot.send_message(chat_id, "Выберите пункт меню:", reply_markup=markup)
    user_state[chat_id] = {"step": "waiting_for_topic"}


@bot.message_handler(func=lambda message: user_state.get(message.chat.id, {}).get("step") == "waiting_for_topic")
def handle_topic_selection(message):
    topic = message.text
    if topic not in responsible_ids:
        bot.send_message(message.chat.id, "Пожалуйста, выберите пункт из меню.")
        return

    user_state[message.chat.id] = {
        "step": "collecting_input",
        "topic": topic,
        "text": [],
        "files": []
    }

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("✅ Подтвердить", "✏️ Изменить", "🔙 Назад в меню")

    bot.send_message(
        message.chat.id,
        f"Отлично, вы выбрали: {topic}.\nОтправьте описание задачи и файлы. Когда закончите — нажмите «✅ Подтвердить».",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: user_state.get(message.chat.id, {}).get("step") == "collecting_input", content_types=['text', 'photo', 'document', 'video'])
def collect_user_input(message):
    state = user_state.get(message.chat.id)
    if not state:
        return

    # Сохраняем текст
    if message.text and message.text not in ["✅ Подтвердить", "✏️ Изменить", "🔙 Назад в меню"]:
        state["text"].append(message.text)

    # Сохраняем ссылки на файлы
    if message.content_type in ['photo', 'document', 'video']:
        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id  # самая большая версия фото
        elif message.content_type == 'document':
            file_id = message.document.file_id
        elif message.content_type == 'video':
            file_id = message.video.file_id

        if file_id:
            file_info = bot.get_file(file_id)
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
            state["files"].append(file_url)
            bot.send_message(message.chat.id, f"Файл получен: {file_url}")


@bot.message_handler(func=lambda message: message.text in ["✅ Подтвердить", "✏️ Изменить", "🔙 Назад в меню"])
def handle_confirmation(message):
    chat_id = message.chat.id
    state = user_state.get(chat_id)

    if not state:
        show_main_menu(chat_id)
        return

    if message.text == "🔙 Назад в меню":
        show_main_menu(chat_id)

    elif message.text == "✏️ Изменить":
        user_state[chat_id]["text"] = []
        user_state[chat_id]["files"] = []
        bot.send_message(chat_id, "Введите новый текст задачи и файлы. После этого нажмите «✅ Подтвердить».")

    elif message.text == "✅ Подтвердить":
        topic = state["topic"]
        text = "\n".join(state["text"])
        files = "\n".join(state["files"])
        description = f"Новый запрос по '{topic}'\n\nОписание:\n{text}"
        if files:
            description += f"\n\nФайлы:\n{files}"

        responsible_id = responsible_ids[topic]

        # Отправка задачи в Bitrix24
        task_data = {
            "fields": {
                "TITLE": f"Новая задача: {topic}",
                "DESCRIPTION": description,
                "RESPONSIBLE_ID": responsible_id
            }
        }

        response = requests.post(BITRIX_WEBHOOK, json=task_data)

        if response.status_code == 200:
            bot.send_message(chat_id, f"✅ Задача по '{topic}' создана!")
        else:
            bot.send_message(chat_id, f"⚠️ Ошибка при создании задачи. Код: {response.status_code}")

        show_main_menu(chat_id)
