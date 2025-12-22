import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")
if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL not set")

PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = WEBHOOK_BASE_URL.rstrip("/") + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. "
        "Займёт буквально пару минут 🙂"
    )

async def handle_webhook(request: web.Request):
    data = await request.json()
    update = types.Update.model_validate(data)  # важно для aiogram v3
    await dp.feed_update(bot, update)
    return web.Response(text="ok")

async def health(request: web.Request):
    return web.Response(text="OK")

async def on_startup(app: web.Application):
    await bot.set_webhook(
        WEBHOOK_URL,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    print(f"✅ Webhook set: {WEBHOOK_URL}")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    print(f"Listening on 0.0.0.0:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()