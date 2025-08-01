# smoke.py
import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.enums.parse_mode import ParseMode

# Логирование для дебага
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
    print("ERROR: TELEGRAM_BOT_TOKEN не задан")
    exit(1)

# Инициализация бота: пробуем DefaultBotProperties, если есть. Иначе просто без parse_mode.
def make_bot(token: str):
    try:
        from aiogram.types import DefaultBotProperties  # может не быть в этой сборке
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        logger.debug("Использую DefaultBotProperties для parse_mode=HTML")
        return bot
    except (ImportError, TypeError) as e:
        logger.debug("Fallback: создаю Bot без default properties (%s)", e)
        return Bot(token=token)  # не передаём parse_mode, чтобы не падало

bot = make_bot(TOKEN)
dp = Dispatcher()

@dp.message()
async def echo(message: types.Message):
    logger.debug("Получено сообщение от %s: %s", message.from_user.id, message.text)
    # Ответим обычным текстом — никаких проблем с parse_mode
    await message.reply(f"Йо, получил: {message.text}")

async def main():
    logger.info("Старт polling, aiogram version: %s", getattr(__import__("aiogram"), "__version__", "unknown"))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
