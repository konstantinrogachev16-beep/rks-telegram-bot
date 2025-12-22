import asyncio
import os
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

PORT = int(os.getenv("PORT", "10000"))  # Render задаёт PORT сам


async def start_bot_polling(dp: Dispatcher, bot: Bot):
    # polling forever
    await dp.start_polling(bot)


def create_app() -> web.Application:
    app = web.Application()

    async def health(request):
        return web.Response(text="OK")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    return app


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: types.Message):
        await message.answer(
            "Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. "
            "Займёт буквально пару минут, ок? 🙂"
        )

    # 1) запускаем polling в фоне
    bot_task = asyncio.create_task(start_bot_polling(dp, bot))

    # 2) поднимаем web-сервер, чтобы Render видел порт
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    logging.info(f"Web server started on 0.0.0.0:{PORT}")

    # 3) ждём polling (он бесконечный)
    await bot_task


if __name__ == "__main__":
    asyncio.run(main())