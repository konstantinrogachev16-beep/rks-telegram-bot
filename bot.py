import os
import re
import logging
from datetime import datetime

from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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

# ----------------- logging -----------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("rks-bot")

# ----------------- env -----------------
load_dotenv()  # локально читает .env; на Render не мешает

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID not set")

OWNER_ID_INT = int(OWNER_ID)

# ----------------- catalog -----------------
SERVICES = [
    ("Тонировка", "SRV_TINT"),
    ("Удаление водного камня", "SRV_WATERSTONE"),
    ("Антидождь", "SRV_RAIN"),
    ("Полировка фар", "SRV_HEADLIGHTS"),
    ("Полировка кузова", "SRV_BODY"),
    ("Керамика", "SRV_CERAMIC"),
    ("Шлифовка/полировка стёкол", "SRV_GLASS"),
]

READY = [
    ("Сегодня/завтра", "READY_NOW"),
    ("На этой неделе", "READY_WEEK"),
    ("Позже", "READY_LATER"),
]

CONTACT_METHODS = [
    ("Звонок", "CM_CALL"),
    ("WhatsApp", "CM_WA"),
    ("Telegram (сюда)", "CM_TG"),
]

# ----------------- states -----------------
ASK_NAME, ASK_CAR, PICK_SERVICES, PICK_READY, ASK_CONTACT, PICK_CONTACT_METHOD = range(6)


def normalize_phone(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)
    only_digits = re.sub(r"\D", "", digits)

    if len(only_digits) < 10:
        return None

    # РФ: 8XXXXXXXXXX -> +7XXXXXXXXXX
    if digits.startswith("8") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits
    if digits.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]

    # если номер не РФ — вернем как есть (с плюсиком/без)
    return digits


def services_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for title, code in SERVICES:
        mark = "✅ " if code in selected else "➕ "
        rows.append([InlineKeyboardButton(mark + title, callback_data=f"SRV:{code}")])
    rows.append([InlineKeyboardButton("Готово ✅", callback_data="SRV:DONE")])
    return InlineKeyboardMarkup(rows)


def ready_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(t, callback_data=f"READY:{c}")] for t, c in READY]
    return InlineKeyboardMarkup(rows)


def contact_method_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(t, callback_data=f"CM:{c}")] for t, c in CONTACT_METHODS]
    return InlineKeyboardMarkup(rows)


def code_to_text(code: str, mapping: list[tuple[str, str]]) -> str:
    for text, c in mapping:
        if c == code:
            return text
    return code


async def safe_send_owner(app: Application, text: str) -> None:
    try:
        await app.bot.send_message(chat_id=OWNER_ID_INT, text=text)
    except Exception:
        logger.exception("Failed to send message to owner")


# ----------------- handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я помогу быстро понять, что лучше сделать с машиной.\n\n"
        "Как тебя зовут? 🙂",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_NAME


async def step_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    await update.message.reply_text("Какая машина? (марка/модель)")
    return ASK_CAR


async def step_car(update: Update, context: ContextTypes.DEFAULT_TYPE):
    car = (update.message.text or "").strip()
    if len(car) < 2:
        await update.message.reply_text("Напиши марку/модель (например: Camry / Solaris)")
        return ASK_CAR

    context.user_data["car"] = car
    context.user_data["services_selected"] = set()

    await update.message.reply_text(
        "Какие услуги интересуют? (можно выбрать несколько, потом нажми «Готово ✅»)",
        reply_markup=services_keyboard(context.user_data["services_selected"]),
    )
    return PICK_SERVICES


async def pick_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    payload = (query.data or "").split(":", 1)[1]
    selected: set[str] = set(context.user_data.get("services_selected") or set())

    if payload == "DONE":
        if not selected:
            await query.message.reply_text(
                "Выбери хотя бы одну услугу 🙂",
                reply_markup=services_keyboard(selected),
            )
            return PICK_SERVICES

        context.user_data["services_selected"] = selected
        await query.message.reply_text("Когда планируешь?", reply_markup=ready_keyboard())
        return PICK_READY

    # toggle
    if payload in selected:
        selected.remove(payload)
    else:
        selected.add(payload)

    context.user_data["services_selected"] = selected
    await query.message.edit_reply_markup(reply_markup=services_keyboard(selected))
    return PICK_SERVICES


async def pick_ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    code = (query.data or "").split(":", 1)[1]
    context.user_data["ready_time"] = code

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Отправить контакт ☎️", request_contact=True)],
            [KeyboardButton("Написать номер текстом")],
            [KeyboardButton("Оставлю Telegram, можно сюда")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await query.message.reply_text(
        "Оставь номер телефона — и я передам заявку менеджеру.\n\n"
        "Можно нажать кнопку «Отправить контакт» или написать номер текстом.",
        reply_markup=kb,
    )
    return ASK_CONTACT


async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # контакт кнопкой
    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        context.user_data["phone"] = phone
        context.user_data["contact_method"] = "phone"
        await update.message.reply_text("Как удобнее связаться?", reply_markup=contact_method_keyboard())
        return PICK_CONTACT_METHOD

    txt = (update.message.text or "").strip()
    low = txt.lower()

    if "телег" in low or "сюда" in low or "tg" in low:
        context.user_data["phone"] = ""
        context.user_data["contact_method"] = "telegram"
        await update.message.reply_text("Ок 👍 Как удобнее связаться?", reply_markup=contact_method_keyboard())
        return PICK_CONTACT_METHOD

    phone = normalize_phone(txt)
    if not phone:
        await update.message.reply_text(
            "Не похоже на номер 🙂\n"
            "Напиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
        )
        return ASK_CONTACT

    context.user_data["phone"] = phone
    context.user_data["contact_method"] = "phone"
    await update.message.reply_text("Как удобнее связаться?", reply_markup=contact_method_keyboard())
    return PICK_CONTACT_METHOD


async def pick_contact_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cm_code = (query.data or "").split(":", 1)[1]
    context.user_data["contact_method_choice"] = cm_code

    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "(нет username)"

    selected_codes: set[str] = set(context.user_data.get("services_selected") or set())
    services_texts = [code_to_text(c, SERVICES) for c in selected_codes]
    services_joined = ", ".join(services_texts)

    ready_text = code_to_text(context.user_data.get("ready_time", ""), READY)
    cm_text = code_to_text(cm_code, CONTACT_METHODS)

    lead_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    lead_text = (
        "🔥 <b>Новый лид RKS Studio</b>\n"
        f"ID: <code>{lead_id}</code>\n"
        f"Имя: <b>{context.user_data.get('name','')}</b>\n"
        f"Авто: <b>{context.user_data.get('car','')}</b>\n"
        f"Интерес: <b>{services_joined}</b>\n"
        f"Срок: <b>{ready_text}</b>\n"
        f"Связь: <b>{cm_text}</b>\n"
        f"Контакт: <b>{context.user_data.get('phone') or 'Telegram'}</b>\n"
        f"TG: {username}\n"
    )

    # отправляем тебе
    await safe_send_owner(context.application, lead_text)

    # клиенту
    await query.message.reply_text(
        "✅ Заявка отправлена!\n"
        "Я передал информацию менеджеру — он свяжется с тобой в ближайшее время.\n\n"
        "Если хочешь — можешь дописать детали (фото/видео тоже можно).",
        reply_markup=ReplyKeyboardRemove(),
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_name)],
            ASK_CAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_car)],
            PICK_SERVICES: [CallbackQueryHandler(pick_services, pattern=r"^SRV:")],
            PICK_READY: [CallbackQueryHandler(pick_ready, pattern=r"^READY:")],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
            PICK_CONTACT_METHOD: [CallbackQueryHandler(pick_contact_method, pattern=r"^CM:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_error_handler(error_handler)

    # ВАЖНО: чтобы не было конфликтов webhook/getUpdates
    # (ты уже удалял вебхук, но пусть бот делает это сам при старте)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()