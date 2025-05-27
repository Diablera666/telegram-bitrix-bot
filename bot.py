import os
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
from dotenv import load_dotenv

load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
BITRIX_WEBHOOK_URL = os.getenv('BITRIX_WEBHOOK_URL')  # Пример: https://getman.bitrix24.kz/rest/270/XXXXXX/task.item.add.json
PARENT_ID = 63  # ID папки Bitrix24, куда загружаются файлы
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояние пользователя
user_states = {}

class UserState:
    def __init__(self):
        self.stage = None
        self.category = None
        self.text = ""
        self.files = []

# Кнопки подтверждения
def get_confirm_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        InlineKeyboardButton("🔙 Назад", callback_data="back")
    )
    markup.add(
        InlineKeyboardButton("🗑 Удалить последний файл", callback_data="delete_last"),
        InlineKeyboardButton("❌ Отменить", callback_data="cancel")
    )
    return markup

# Обработчик /start
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    user_states[user_id] = UserState()
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("Вопрос 1", callback_data="category_1"),
        InlineKeyboardButton("Вопрос 2", callback_data="category_2"),
    )
    markup.add(
        InlineKeyboardButton("Вопрос 3", callback_data="category_3"),
        InlineKeyboardButton("Другое", callback_data="category_other"),
    )
    bot.send_message(user_id, "Выберите категорию:", reply_markup=markup)

# Обработка кнопок
@bot.callback_query_handler(func=lambda call: call.data.startswith("category_"))
def handle_category(call):
    user_id = call.from_user.id
    user_states[user_id] = UserState()
    state = user_states[user_id]
    category = call.data.replace("category_", "")
    state.category = category
    state.stage = "collecting"
    bot.send_message(user_id, "Введите описание задачи и прикрепите файлы (если нужно). Когда закончите, нажмите одну из кнопок ниже.", reply_markup=get_confirm_keyboard())

@bot.callback_query_handler(func=lambda call: call.data in ["confirm", "cancel", "delete_last", "back"])
def handle_actions(call):
    user_id = call.from_user.id
    state = user_states.get(user_id)

    if not state:
        bot.send_message(user_id, "Нет активной задачи.")
        return

    if call.data == "cancel":
        user_states.pop(user_id, None)
        bot.send_message(user_id, "Создание задачи отменено.")
    elif call.data == "delete_last":
        if state.files:
            removed = state.files.pop()
            bot.send_message(user_id, f"Последний файл удалён: {removed['name']}")
        else:
            bot.send_message(user_id, "Нет файлов для удаления.")
    elif call.data == "back":
        handle_start(call.message)
    elif call.data == "confirm":
        send_to_bitrix(user_id, state)
        user_states.pop(user_id, None)

# Обработка текстов
@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if state and state.stage == "collecting":
        state.text += f"\n{message.text}"

# Обработка файлов и медиа
@bot.message_handler(content_types=['document', 'photo', 'video', 'audio', 'voice'])
def handle_files(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state or state.stage != "collecting":
        return

    file_info = None
    file_id = None
    name = None

    if message.content_type == 'document':
        file_info = bot.get_file(message.document.file_id)
        name = message.document.file_name
        file_id = message.document.file_id
        size = message.document.file_size
    elif message.content_type == 'photo':
        file_info = bot.get_file(message.photo[-1].file_id)
        name = "photo.jpg"
        file_id = message.photo[-1].file_id
        size = None
    elif message.content_type == 'video':
        file_info = bot.get_file(message.video.file_id)
        name = message.video.file_name or "video.mp4"
        file_id = message.video.file_id
        size = message.video.file_size
    elif message.content_type == 'audio':
        file_info = bot.get_file(message.audio.file_id)
        name = message.audio.file_name or "audio.mp3"
        file_id = message.audio.file_id
        size = message.audio.file_size
    elif message.content_type == 'voice':
        file_info = bot.get_file(message.voice.file_id)
        name = "voice.ogg"
        file_id = message.voice.file_id
        size = message.voice.file_size

    if size and size > MAX_FILE_SIZE:
        bot.send_message(user_id, f"Файл слишком большой: {round(size / 1024 / 1024, 2)} МБ. Максимум 50 МБ.")
        return

    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    state.files.append({"url": file_url, "name": name})
    bot.send_message(user_id, f"Файл добавлен: {name}")

# Загрузка файла на Bitrix24
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
            data = {
                'id': folder_id,
                'generateUniqueName': 'Y'
            }
            upload_url = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.folder.uploadfile.json')
            response = requests.post(upload_url, data=data, files=files, timeout=30)
            response.raise_for_status()

            result = response.json()
            if 'result' not in result or 'file' not in result['result']:
                logger.error("No file ID in response: %s", result)
                return None

            file_id = result['result']['file']['ID']

        # Получаем attached ID
        attach_url = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.attachedObject.add.json')
        attach_data = {
            'OBJECT_ID': file_id,
            'ENTITY_ID': 0,
            'ENTITY_TYPE': 'task',
            'MODULE_ID': 'tasks'
        }
        attach_resp = requests.post(attach_url, data=attach_data, timeout=15)
        attach_resp.raise_for_status()
        attach_result = attach_resp.json()

        if 'result' in attach_result and 'ID' in attach_result['result']:
            return int(attach_result['result']['ID'])
        else:
            logger.error("Failed to get attachedId: %s", attach_result)
            return None

    except Exception as e:
        logger.warning("Failed to upload file: %s", file_url)
        logger.exception(e)
        return None
    finally:
        try:
            os.remove(local_filename)
        except Exception:
            pass

# Отправка задачи в Bitrix24
def send_to_bitrix(user_id, state):
    text = state.text.strip()
    attached_ids = []

    for file in state.files:
        attached_id = upload_file_to_bitrix(file['url'])
        if attached_id:
            attached_ids.append(f"n{attached_id}")

    task_data = {
        'fields': {
            'TITLE': f"Новая задача от пользователя {user_id}",
            'DESCRIPTION': text or "Без описания",
            'RESPONSIBLE_ID': get_responsible_id(state.category),
            'UF_TASK_WEBDAV_FILES': attached_ids
        }
    }

    try:
        response = requests.post(BITRIX_WEBHOOK_URL, json=task_data)
        response.raise_for_status()
        bot.send_message(user_id, "✅ Задача успешно создана в Bitrix24!")
    except Exception as e:
        logger.exception("Ошибка при создании задачи")
        bot.send_message(user_id, "❌ Ошибка при создании задачи. Попробуйте позже.")

def get_responsible_id(category):
    if category in ['1', '3']:
        return 270
    else:
        return 12

# Запуск
if __name__ == '__main__':
    import threading
    bot.remove_webhook()
    threading.Thread(target=bot.infinity_polling, name="run_bot").start()
