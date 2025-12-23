import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================== ENV ==================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "327140660")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

# ================== Render port stub ==================
def run_port_stub():
    """Small HTTP server so Render Web Service detects an open port."""
    port = int(os.getenv("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            return

    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# ================== Helpers ==================
def normalize_phone(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)
    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) < 10:
        return None

    # RU: 8XXXXXXXXXX -> +7XXXXXXXXXX
    if digits.startswith("8") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits
    if digits.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]
    # If already like +<country>...
    if digits.startswith("+") and len(only_digits) >= 10:
        return digits

    return digits

def safe_text(update: Update) -> str:
    return (update.message.text or "").strip() if update.message else ""

# ================== Conversation states ==================
ASK_NAME = 0
PICK_SERVICES = 1
TINT_PICK_ZONES = 2
ASK_TIME = 3
ASK_CONTACT = 4

# ================== Services data ==================
SERVICES = [
    "Тонировка",
    "Полировка кузова",
    "Керамика (защита)",
    "Удаление водного камня (стёкла)",
    "Антидождь",
    "Полировка фар",
    "Шлифовка/полировка стекла",
]

# callback keys
CB_DONE = "done_services"
CB_RESET = "reset_services"
CB_SVC_PREFIX = "svc:"  # svc:<service_name>

# tint zones
TINT_ZONES = [
    "Полусфера зад",
    "Полусфера перед",
    "Боковые задние",
    "Боковые передние",
    "Лобовое",
    "Заднее",
]
CB_TINT_DONE = "done_tint"
CB_TINT_RESET = "reset_tint"
CB_TINT_PREFIX = "tint:"  # tint:<zone>

# ================== UI builders ==================
def build_services_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for s in SERVICES:
        mark = "✅" if s in selected else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {s}", callback_data=f"{CB_SVC_PREFIX}{s}")])
    rows.append([
        InlineKeyboardButton("Готово ✅", callback_data=CB_DONE),
        InlineKeyboardButton("Сбросить ↩️", callback_data=CB_RESET),
    ])
    return InlineKeyboardMarkup(rows)

def build_tint_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for z in TINT_ZONES:
        mark = "✅" if z in selected else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {z}", callback_data=f"{CB_TINT_PREFIX}{z}")])
    rows.append([
        InlineKeyboardButton("Готово ✅", callback_data=CB_TINT_DONE),
        InlineKeyboardButton("Сбросить ↩️", callback_data=CB_TINT_RESET),
    ])
    return InlineKeyboardMarkup(rows)

# ================== Handlers ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя.\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = safe_text(update)
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    context.user_data["services_selected"] = set()

    kb = build_services_keyboard(context.user_data["services_selected"])
    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=kb,
    )
    return PICK_SERVICES

async def services_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected: set[str] = context.user_data.get("services_selected", set())
    data = query.data or ""

    if data == CB_RESET:
        selected.clear()
        context.user_data["services_selected"] = selected
        await query.edit_message_reply_markup(reply_markup=build_services_keyboard(selected))
        return PICK_SERVICES

    if data.startswith(CB_SVC_PREFIX):
        svc = data[len(CB_SVC_PREFIX):]
        if svc in selected:
            selected.remove(svc)
        else:
            selected.add(svc)
        context.user_data["services_selected"] = selected
        await query.edit_message_reply_markup(reply_markup=build_services_keyboard(selected))
        return PICK_SERVICES

    if data == CB_DONE:
        if not selected:
            await query.answer("Нужно выбрать хотя бы одну услугу 🙂", show_alert=True)
            return PICK_SERVICES

        # If tint is selected -> ask zones first
        if "Тонировка" in selected:
            context.user_data["tint_zones"] = set()
            await query.message.reply_text(
                "Тонировка ✅\nВыбери что нужно затонировать (можно несколько) и нажми «Готово ✅».",
                reply_markup=build_tint_keyboard(context.user_data["tint_zones"]),
            )
            return TINT_PICK_ZONES

        # otherwise go to time
        await query.message.reply_text(
            "Когда тебе удобно приехать? (пример: «завтра после 18:00», «в выходные утром»)"
        )
        return ASK_TIME

    return PICK_SERVICES

async def tint_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected: set[str] = context.user_data.get("tint_zones", set())
    data = query.data or ""

    if data == CB_TINT_RESET:
        selected.clear()
        context.user_data["tint_zones"] = selected
        await query.edit_message_reply_markup(reply_markup=build_tint_keyboard(selected))
        return TINT_PICK_ZONES

    if data.startswith(CB_TINT_PREFIX):
        zone = data[len(CB_TINT_PREFIX):]
        if zone in selected:
            selected.remove(zone)
        else:
            selected.add(zone)
        context.user_data["tint_zones"] = selected

        # quick recommendations while selecting
        # (short + useful, not spam)
        if zone == "Лобовое":
            await query.answer("Лобовое: можно атермальную плёнку — меньше жара и бликов.", show_alert=False)

        await query.edit_message_reply_markup(reply_markup=build_tint_keyboard(selected))
        return TINT_PICK_ZONES

    if data == CB_TINT_DONE:
        if not selected:
            await query.answer("Выбери хотя бы одну зону 🙂", show_alert=True)
            return TINT_PICK_ZONES

        # After tint zones -> go to time
        await query.message.reply_text(
            "Отлично. Когда тебе удобно приехать? (пример: «завтра после 18:00», «в выходные утром»)"
        )
        return ASK_TIME

    return TINT_PICK_ZONES

async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = safe_text(update)
    if len(txt) < 3:
        await update.message.reply_text("Напиши удобное время чуть подробнее 🙂")
        return ASK_TIME

    context.user_data["time"] = txt

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Отправить контакт ☎️", request_contact=True)],
            [KeyboardButton("Написать номер текстом")],
            [KeyboardButton("Оставлю Telegram, можно сюда")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "Чтобы подтвердить запись/уточнить детали — оставь контакт:\n"
        "• нажми «Отправить контакт ☎️»\n"
        "• или напиши номер текстом\n"
        "• или просто скажи «можно сюда в Telegram»",
        reply_markup=kb,
    )
    return ASK_CONTACT

async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact_method = "telegram"
    phone = ""

    if update.message and update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        contact_method = "phone"
    else:
        txt = safe_text(update)
        low = txt.lower()
        if "телег" in low or "сюда" in low or "tg" in low:
            contact_method = "telegram"
            phone = ""
        else:
            p = normalize_phone(txt)
            if not p:
                await update.message.reply_text(
                    "Не похоже на номер 🙂\n"
                    "Напиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
                )
                return ASK_CONTACT
            contact_method = "phone"
            phone = p

    context.user_data["contact_method"] = contact_method
    context.user_data["phone"] = phone

    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "(нет username)"
    tg_id = user.id if user else "?"

    services_selected: set[str] = context.user_data.get("services_selected", set())
    tint_zones: set[str] = context.user_data.get("tint_zones", set())

    services_lines = []
    for s in sorted(services_selected):
        if s == "Тонировка" and tint_zones:
            services_lines.append(f"• {s}: {', '.join(sorted(tint_zones))}")
        else:
            services_lines.append(f"• {s}")

    lead_text = (
        "🔥 НОВЫЙ ЛИД (RKS)\n"
        f"Имя: {context.user_data.get('name','')}\n"
        f"TG: {username}\n"
        f"TG_ID: {tg_id}\n"
        f"Услуги:\n" + ("\n".join(services_lines) if services_lines else "• (нет)") + "\n"
        f"Время: {context.user_data.get('time','')}\n"
        f"Контакт: {(phone if phone else 'Telegram')}\n"
    )

    # send to admin
    try:
        await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=lead_text)
    except Exception as e:
        # still don't break user flow
        print("ADMIN SEND ERROR:", e)
        print(lead_text)

    await update.message.reply_text(
        "✅ Принято! Я отправил заявку.\n"
        "Мы свяжемся с тобой в ближайшее время.\n\n"
        "Если хочешь — можешь дописать детали (фото/видео тоже можно).",
        reply_markup=None,
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END

async def on_startup(app: Application):
    # Make sure webhook is not set (avoid webhook/polling mixing)
    try:
        await app.bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        print("delete_webhook error:", e)

def main():
    # Run port stub for Render Web Service
    t = threading.Thread(target=run_port_stub, daemon=True)
    t.start()

    app = Application.builder().token(TOKEN).post_init(on_startup).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            PICK_SERVICES: [CallbackQueryHandler(services_callback)],
            TINT_PICK_ZONES: [CallbackQueryHandler(tint_callback)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()