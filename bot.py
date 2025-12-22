import os
import asyncio
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# Публичный URL твоего Render сервиса, например:
# https://rks-telegram-bot.onrender.com
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE_URL")
if not WEBHOOK_BASE:
    raise RuntimeError("WEBHOOK_BASE_URL is not set")

# Любая длинная строка (секрет для URL вебхука)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change_me_please")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH

PORT = int(os.getenv("PORT", "10000"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. "
        "Займёт буквально пару минут, ок? 🙂"
    )

async def on_startup(app: web.Application):
    # ставим вебхук
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()

async def telegram_webhook(request: web.Request) -> web.Response:
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return web.Response(text="ok")

def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, telegram_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()