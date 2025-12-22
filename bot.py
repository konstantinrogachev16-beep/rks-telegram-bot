import os
import re
from datetime import datetime
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

# =========================
# ENV
# =========================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "327140660"))

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

# =========================
# STATES
# =========================
(
    ASK_NAME,
    SERVICES_PICK,
    ASK_CONTEXT,
    ASK_PAIN,
    ASK_RESULT,
    QUIZ_BUDGET,
    QUIZ_PRIORITY,
    ASK_TIME,
    ASK_CONTACT,
) = range(9)

# =========================
# SERVICES (multi-select)
# =========================
SERVICES = [
    ("tint", "Тонировка"),
    ("polish", "Полировка кузова"),
    ("ceramic", "Керамика (защита)"),
    ("glass", "Удаление водного камня (стёкла)"),
    ("anti_rain", "Антидождь"),
    ("headlights", "Полировка фар"),
    ("glass_polish", "Шлифовка/полировка стекла"),
]

# =========================
# UTILS
# =========================
def normalize_phone(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)
    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) < 10:
        return None

    # РФ приведение 8XXXXXXXXXX -> +7XXXXXXXXXX
    if digits.startswith("8") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits
    if digits.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]

    # иначе оставим как есть, но проверим что длина ок
    return digits


def safe_username(update: Update) -> str:
    user = update.effective_user
    if user and user.username:
        return f"@{user.username}"
    return "(нет username)"


def services_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, title in SERVICES:
        mark = "✅ " if key in selected else "⬜ "
        rows.append([InlineKeyboardButton(mark + title, callback_data=f"svc:{key}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc_done"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="svc_reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_recommendation(data: dict) -> str:
    selected = data.get("services", set())
    budget = data.get("budget", "")
    priority = data.get("priority", "")

    # Базовая логика рекомендаций
    rec = []
    if "glass" in selected:
        rec.append("• По стёклам: удаление водного камня + (по желанию) антидождь для эффекта «как новое».")
    if "anti_rain" in selected and "glass" not in selected:
        rec.append("• Антидождь лучше наносить на чистое стекло — если есть налёт, сначала убираем водный камень.")
    if "polish" in selected:
        rec.append("• По кузову: полировка восстановит глубину цвета и уберёт мелкие царапины/матовость.")
    if "ceramic" in selected:
        rec.append("• Защита: керамика усилит блеск и упростит мойку, держит эффект дольше.")
    if "tint" in selected:
        rec.append("• Комфорт: тонировка снизит перегрев салона и улучшит приватность.")
    if "headlights" in selected:
        rec.append("• Фары: полировка улучшит свет и внешний вид (особенно если есть помутнение).")
    if "glass_polish" in selected:
        rec.append("• Стекло: шлифовка/полировка поможет при мелких царапинах (глубокие могут остаться частично).")

    if not rec:
        rec.append("• Под твою задачу можно собрать оптимальный комплекс (скажешь, что важнее — блеск/защита/стёкла/комфорт).")

    # Уточнение по бюджету/приоритету (мягко, без лишних вопросов)
    tail = []
    if budget:
        tail.append(f"Бюджет: {budget}.")
    if priority:
        tail.append(f"Приоритет: {priority}.")
    tail_text = (" " + " ".join(tail)) if tail else ""

    return "✅ Рекомендация (предварительно):\n" + "\n".join(rec) + (f"\n\n{tail_text}".strip() if tail_text else "")


def parse_datetime_text(text: str) -> str | None:
    """
    Принимаем:
    - 'сегодня 19:00' / 'завтра 12:30' (как текст — просто сохраним)
    - '25.12 18:00'
    - '25.12.2025 18:00'
    - '25/12 18:00'
    Валидация минимальная: должна быть дата и время HH:MM.
    """
    t = (text or "").strip().lower()
    if not t:
        return None

    if "сегодня" in t or "завтра" in t:
        # обязательно наличие времени
        if re.search(r"\b([01]\d|2[0-3]):[0-5]\d\b", t):
            return text.strip()
        return None

    # дата + время
    if re.search(r"\b(\d{1,2}[./]\d{1,2})([./]\d{2,4})?\s+([01]\d|2[0-3]):[0-5]\d\b", t):
        return text.strip()

    return None


async def notify_admin(app: Application, admin_id: int, text: str):
    try:
        await app.bot.send_message(chat_id=admin_id, text=text)
    except Exception as e:
        # Не падаем, просто печатаем
        print("ADMIN SEND ERROR:", e)
        print("LEAD TEXT:", text)


# =========================
# HANDLERS
# =========================
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Я на связи. Напиши /start чтобы начать.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["services"] = set()

    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя.\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name

    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=services_keyboard(context.user_data["services"]),
    )
    return SERVICES_PICK


async def services_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    selected: set[str] = context.user_data.get("services", set())

    if data.startswith("svc:"):
        key = data.split(":", 1)[1]
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        context.user_data["services"] = selected

        await query.edit_message_text(
            "Выбери услуги (можно несколько) и нажми «Готово ✅».",
            reply_markup=services_keyboard(selected),
        )
        return SERVICES_PICK

    if data == "svc_reset":
        context.user_data["services"] = set()
        await query.edit_message_text(
            "Сбросил выбор. Выбери услуги и нажми «Готово ✅».",
            reply_markup=services_keyboard(context.user_data["services"]),
        )
        return SERVICES_PICK

    if data == "svc_done":
        # Переходим в квиз
        await query.edit_message_text(
            "Ок 👍 Теперь короткий квиз, чтобы дать точную рекомендацию.\n\n"
            "Расскажи в двух словах про машину и ситуацию.\n"
            "Например: «Camry 2018, хочу освежить внешний вид / есть царапины / стекла в налёте»"
        )
        return ASK_CONTEXT

    return SERVICES_PICK


async def ask_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 3:
        await update.message.reply_text("Чуть подробнее 🙂 Что за машина и что с ней сейчас?")
        return ASK_CONTEXT

    context.user_data["context"] = txt
    await update.message.reply_text(
        "Что больше всего беспокоит прямо сейчас? (1–2 предложения)\n"
        "Например: «мелкие царапины», «матовый кузов», «налёт на стекле», «хочу приватность в салоне»"
    )
    return ASK_PAIN


async def ask_pain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 3:
        await update.message.reply_text("Опиши одним-двумя предложениями 🙂")
        return ASK_PAIN

    context.user_data["pain"] = txt
    await update.message.reply_text(
        "Какой результат хочешь получить в идеале?\n"
        "Например: «чтобы блестела как новая», «чистые стёкла без налёта», «без мелких царапин»"
    )
    return ASK_RESULT


async def ask_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 3:
        await update.message.reply_text("Супер коротко: какой идеальный итог? 🙂")
        return ASK_RESULT

    context.user_data["result"] = txt

    # Квиз: бюджет (кнопками)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            ["До 5 000", "5 000 – 10 000"],
            ["10 000 – 20 000", "20 000+"],
            ["Пока не знаю"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Квиз (1/2): примерно какой бюджет планируешь?",
        reply_markup=kb,
    )
    return QUIZ_BUDGET


async def quiz_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not txt:
        await update.message.reply_text("Выбери вариант кнопкой 🙂")
        return QUIZ_BUDGET

    context.user_data["budget"] = txt

    # Квиз: приоритет (кнопками)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            ["Максимальный блеск", "Защита надолго"],
            ["Быстро и бюджетно", "Стёкла/видимость"],
            ["Комфорт/приватность"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Квиз (2/2): что важнее всего?",
        reply_markup=kb,
    )
    return QUIZ_PRIORITY


async def quiz_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not txt:
        await update.message.reply_text("Выбери вариант кнопкой 🙂")
        return QUIZ_PRIORITY

    context.user_data["priority"] = txt

    # Показать рекомендацию
    rec = build_recommendation(context.user_data)
    await update.message.reply_text(rec, reply_markup=None)

    # Запись по времени
    await update.message.reply_text(
        "Теперь давай запишем удобное время.\n\n"
        "Напиши так, как удобно:\n"
        "• «сегодня 19:00»\n"
        "• «завтра 12:30»\n"
        "• «25.12 18:00»"
    )
    return ASK_TIME


async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    dt = parse_datetime_text(txt)
    if not dt:
        await update.message.reply_text(
            "Не понял время 🙂\n"
            "Примеры: «сегодня 19:00», «завтра 12:30», «25.12 18:00»"
        )
        return ASK_TIME

    context.user_data["time"] = dt

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Отправить контакт ☎️", request_contact=True)],
            ["Написать номер текстом"],
            ["Оставлю Telegram, можно сюда"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "Отлично 👍 Остался контакт для подтверждения записи:\n"
        "• нажми «Отправить контакт ☎️»\n"
        "• или напиши номер текстом\n"
        "• или скажи «можно сюда в Telegram»",
        reply_markup=kb,
    )
    return ASK_CONTACT


async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # контакт кнопкой
    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        context.user_data["phone"] = phone
        context.user_data["contact_method"] = "phone"
    else:
        txt = (update.message.text or "").strip()
        low = txt.lower()

        if "телег" in low or "сюда" in low or "tg" in low:
            context.user_data["contact_method"] = "telegram"
            context.user_data["phone"] = ""
        else:
            phone = normalize_phone(txt)
            if not phone:
                await update.message.reply_text(
                    "Не похоже на номер 🙂\n"
                    "Напиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
                )
                return ASK_CONTACT
            context.user_data["phone"] = phone
            context.user_data["contact_method"] = "phone"

    # Собираем лид
    uname = safe_username(update)
    selected = context.user_data.get("services", set())
    selected_titles = [title for key, title in SERVICES if key in selected]
    if not selected_titles:
        selected_titles = ["(не выбрано)"]

    lead_text = (
        "🔥 НОВЫЙ ЛИД\n"
        f"Имя: {context.user_data.get('name','')}\n"
        f"TG: {uname}\n"
        f"Услуги: {', '.join(selected_titles)}\n"
        f"Контекст: {context.user_data.get('context','')}\n"
        f"Боль: {context.user_data.get('pain','')}\n"
        f"Результат: {context.user_data.get('result','')}\n"
        f"Квиз — бюджет: {context.user_data.get('budget','')}\n"
        f"Квиз — приоритет: {context.user_data.get('priority','')}\n"
        f"Время: {context.user_data.get('time','')}\n"
        f"Контакт: {context.user_data.get('phone','') or 'Telegram'}\n"
    )

    # Отправляем админу (тебе)
    await notify_admin(context.application, ADMIN_ID, lead_text)

    await update.message.reply_text(
        "✅ Готово! Я зафиксировал заявку и запись.\n"
        "Менеджер свяжется с тобой для подтверждения.\n\n"
        "Если хочешь — можешь прямо сейчас отправить фото/видео машины (я добавлю к заявке).",
        reply_markup=None,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            SERVICES_PICK: [CallbackQueryHandler(services_pick_callback)],
            ASK_CONTEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_context)],
            ASK_PAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_pain)],
            ASK_RESULT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_result)],
            QUIZ_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_budget)],
            QUIZ_PRIORITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_priority)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("health", health))
    app.add_handler(conv)

    # polling (без вебхуков)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()