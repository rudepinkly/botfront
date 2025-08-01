# bot.py
import os
import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import MenuButtonWebApp, WebAppInfo

# совместимость parse_mode (если есть)
try:
    from aiogram.types import DefaultBotProperties
    from aiogram.enums.parse_mode import ParseMode

    def make_bot(token: str):
        return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
except ImportError:
    def make_bot(token: str):
        return Bot(token=token)  # fallback

# config
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://yourgame.pages.dev")  # URL, где лежит index.html
if not TOKEN:
    print("Укажи TELEGRAM_BOT_TOKEN в окружении")
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

bot = make_bot(TOKEN)
dp = Dispatcher()


@dp.message(Command(commands=["start", "sr_start"]))
async def cmd_start(message: types.Message):
    # ставим WebApp-кнопку в меню чата
    try:
        await bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="🎮 Играть", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?chat_id={message.chat.id}"))
        )
    except Exception as e:
        logger.warning("Не удалось установить WebApp кнопку: %s", e)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Открыть арена", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?chat_id={message.chat.id}"))]
    ])
    await message.reply(
        f"🔥 Зацени свою арены соц-рейтинга, {message.from_user.full_name or message.from_user.username} — жми кнопку ниже.", 
        reply_markup=kb
    )


@dp.message(Command(commands=["help", "sr_help"]))
async def cmd_help(message: types.Message):
    await message.reply(
        "🧠 /sr_start — открыть WebApp (твой профиль, колесо, слот, рейтинг и звёздочки)\n"
        "💎 В WebApp: /daily, престиж, колесо, слот, локальный топ\n"
        "📊 Команды можно расширить: можно добавить /sr_leaderboard, /sr_gift и т.п. через API\n"
        "🛠 Админка будет позже (выдача звёздочек, коррекция рейтинга)."
    )


@dp.message()
async def fallback(message: types.Message):
    if message.text and message.text.startswith("/"):
        await message.reply("Не понимаю, выкатывай /sr_start чтобы играть.")
    else:
        await message.reply("Жми /sr_start, чтобы открыть соц-арену.")


async def main():
    logger.info("Запуск бота")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
