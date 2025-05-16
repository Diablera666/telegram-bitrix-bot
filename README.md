# Telegram Bitrix Bot

Этот бот позволяет создавать задачи в Bitrix24 из Telegram.

## Запуск

1. Создайте `.env` на основе `.env.template`
2. Установите зависимости: `pip install -r requirements.txt`
3. Запустите: `python bot.py`

## Переменные окружения

- TELEGRAM_BOT_TOKEN — токен Telegram-бота
- BITRIX_WEBHOOK_URL — URL вебхука Bitrix24