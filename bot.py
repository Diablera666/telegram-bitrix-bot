import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
import aiohttp
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

menu_buttons = ["Вопрос 1", "Вопрос 2", "Вопрос 3", "Другое"]
RESPONSIBLE_IDS = {
    "Вопрос 1": 270,
    "Вопрос 2": 12,
    "Вопрос 3": 270,
    "Другое": 12
}

markup = ReplyKeyboardMarkup(resize_keyboard=True)
markup.add(*[KeyboardButton(text=b) for b in menu_buttons])

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.answer("Выберите тип запроса:", reply_markup=markup)

@dp.message_handler(lambda message: message.text in menu_buttons)
async def handle_question(message: types.Message):
    task_type = message.text
    await message.answer("Опишите суть задачи:")
    await bot.send_message(message.from_user.id, "Жду описание...")
    dp.register_message_handler(lambda m: process_description(m, task_type), content_types=types.ContentTypes.TEXT, state=None)

async def process_description(message: types.Message, task_type: str):
    description = message.text
    responsible_id = RESPONSIBLE_IDS.get(task_type, 12)
    task_data = {
        "fields": {
            "TITLE": f"Telegram: {task_type}",
            "DESCRIPTION": description,
            "RESPONSIBLE_ID": responsible_id
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(BITRIX_WEBHOOK_URL, json=task_data) as resp:
            if resp.status == 200:
                await message.answer("Задача успешно создана ✅")
            else:
                await message.answer("Ошибка при создании задачи ❌")