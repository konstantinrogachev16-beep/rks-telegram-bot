import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("rks_bot")

# ===================== ENV =====================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

# кому слать лиды
OWNER_ID = int(os.getenv("OWNER_ID", "327140660"))

# ===================== STATES (NO UNPACK BUG) =====================
_STATE_NAMES = [
    "ASK_NAME",
    "SELECT_SERVICES",

    # tint branch
    "TINT_GLASS_MULTI",
    "TINT_LEGAL",
    "TINT_PRIORITY",

    # finish
    "ASK_TIME",
    "ASK_CONTACT",
]
globals().update({name: i for i, name in enumerate(_STATE_NAMES)})

# ===================== HELPERS =====================
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
        digits = "+7" + only_digits[1:]
    elif digits.startswith("7") and len(only_digits) == 11:
        digits = "+7" + only_digits
    elif digits.startswith("+7") and len(only_digits) == 11:
        digits = "+7" + only_digits[-10:]

    return digits

def ud_get_set(context: ContextTypes.DEFAULT_TYPE, key: str) -> set:
    val = context.user_data.get(key)
    if isinstance(val, set):
        return val
    s = set()
    context.user_data[key] = s
    return s

def pretty_services(services: Set[str]) -> str:
    if not services:
        return "—"
    return "• " + "\n• ".join(sorted(services))

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ===================== SERVICES UI =====================
SERVICES = [
    "Тонировка",
    "Полировка кузова",
    "Керамика (защита)",
    "Удаление водного камня (стёкла)",
    "Антидождь",
    "Полировка фар",
    "Шлифовка/полировка стекла",
]

def services_keyboard(selected: Set[str]) -> InlineKeyboardMarkup:
    rows = []
    for s in SERVICES:
        mark = "✅" if s in selected else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {s}", callback_data=f"svc:{s}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc:done"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="svc:reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)

# ===================== TINT (MULTI) UI =====================
TINT_GLASSES = [
    "Полусфера зад (зад + 2 боковых зад)",
    "Передние боковые",
    "Задние боковые",
    "Лобовое",
    "Заднее стекло",
]

def tint_keyboard(selected: Set[str]) -> InlineKeyboardMarkup:
    rows = []
    for g in TINT_GLASSES:
        mark = "✅" if g in selected else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {g}", callback_data=f"tint:{g}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="tint:done"),
            InlineKeyboardButton("Назад ◀️", callback_data="tint:back"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="tint:reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)

def tint_recommendation(glasses: Set[str]) -> str:
    # короткие рекомендации "по ходу"
    tips = []
    if "Лобовое" in glasses:
        tips.append("• Лобовое: обычно выбирают атермальную плёнку — меньше жарит солнце, видимость ок.")
    if "Передние боковые" in glasses:
        tips.append("• Передние боковые: важно помнить про требования закона по светопропусканию.")
    if "Полусфера зад (зад + 2 боковых зад)" in glasses or "Заднее стекло" in glasses:
        tips.append("• Задняя часть: комфортнее в салоне + меньше бликов ночью от фар сзади.")
    if not tips:
        tips.append("• Подберём плёнку под задачи: комфорт/приватность/законность.")
    return "\n".join(tips)

# ===================== FLOW HELPERS =====================
def build_lead_text(context: ContextTypes.DEFAULT_TYPE, update: Update) -> str:
    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "(нет username)"

    services = context.user_data.get("services", set())
    tint_glasses = context.user_data.get("tint_glasses", set())
    tint_legal = context.user_data.get("tint_legal", "")
    tint_priority = context.user_data.get("tint_priority", "")

    contact_method = context.user_data.get("contact_method", "")
    phone = context.user_data.get("phone", "")
    time_pref = context.user_data.get("time_pref", "")

    lines = [
        "🔥 НОВЫЙ ЛИД",
        f"Время: {now_str()}",
        f"Имя: {context.user_data.get('name','')}",
        f"TG: {username} | id={user.id if user else '—'}",
        "",
        "Услуги:",
        pretty_services(services),
    ]

    if "Тонировка" in services:
        lines += [
            "",
            "Тонировка:",
            f"• Зоны: {', '.join(sorted(tint_glasses)) if tint_glasses else '—'}",
            f"• Законность важна?: {tint_legal or '—'}",
            f"• Приоритет: {tint_priority or '—'}",
        ]

    lines += [
        "",
        f"Время/дата: {time_pref or '—'}",
        f"Контакт: {phone or 'Telegram'}",
        f"Способ: {contact_method or '—'}",
    ]

    return "\n".join(lines)

async def go_to_next_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Ветвление по выбранным услугам.
    Сейчас реализуем детально только 'Тонировка' (Шаг 2).
    Остальные пока пропускаем и идём к времени/контакту.
    """
    services: Set[str] = context.user_data.get("services", set())
    context.user_data["branch_queue"] = [s for s in SERVICES if s in services]
    return await run_next_branch(update, context)

async def run_next_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    queue: List[str] = context.user_data.get("branch_queue", [])
    while queue:
        current = queue[0]
        if current == "Тонировка":
            await ask_tint_glasses(update, context)
            return TINT_GLASS_MULTI
        else:
            # пока заглушка: пропускаем и идём дальше
            queue.pop(0)
            continue

    # веток нет — идём дальше
    return await ask_time(update, context)

# ===================== HANDLERS =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя.\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    context.user_data["services"] = set()

    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=services_keyboard(context.user_data["services"]),
    )
    return SELECT_SERVICES

async def services_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    selected: Set[str] = context.user_data.get("services", set())
    if not isinstance(selected, set):
        selected = set()
        context.user_data["services"] = selected

    if data == "svc:reset":
        selected.clear()
        await q.edit_message_reply_markup(reply_markup=services_keyboard(selected))
        return SELECT_SERVICES

    if data == "svc:done":
        if not selected:
            await q.answer("Выбери хотя бы 1 услугу 🙂", show_alert=True)
            return SELECT_SERVICES

        # переходим к ветвлению
        await q.edit_message_text(
            f"Принято 👍\n\nВыбрано:\n{pretty_services(selected)}",
            reply_markup=None,
        )
        return await go_to_next_branch(update, context)

    if data.startswith("svc:"):
        svc = data.split("svc:", 1)[1]
        if svc in selected:
            selected.remove(svc)
        else:
            selected.add(svc)
        await q.edit_message_reply_markup(reply_markup=services_keyboard(selected))
        return SELECT_SERVICES

    return SELECT_SERVICES

# -------- TINT BRANCH --------
async def ask_tint_glasses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    selected = context.user_data.get("tint_glasses", set())
    if not isinstance(selected, set):
        selected = set()
        context.user_data["tint_glasses"] = selected

    text = (
        "Тонировка: выбери что нужно (можно несколько) и нажми «Готово ✅».\n\n"
        "Подсказка: «Полусфера зад» = заднее стекло + 2 задних боковых."
    )

    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=tint_keyboard(selected))
    else:
        await update.message.reply_text(text, reply_markup=tint_keyboard(selected))

async def tint_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    selected = context.user_data.get("tint_glasses", set())
    if not isinstance(selected, set):
        selected = set()
        context.user_data["tint_glasses"] = selected

    if data == "tint:reset":
        selected.clear()
        await q.edit_message_reply_markup(reply_markup=tint_keyboard(selected))
        return TINT_GLASS_MULTI

    if data == "tint:back":
        # вернём на выбор услуг (по желанию)
        await q.edit_message_text(
            "Ок, вернул к выбору услуг. Выбери и нажми «Готово ✅».",
            reply_markup=services_keyboard(context.user_data.get("services", set())),
        )
        return SELECT_SERVICES

    if data == "tint:done":
        if not selected:
            await q.answer("Выбери хотя бы 1 пункт 🙂", show_alert=True)
            return TINT_GLASS_MULTI

        tips = tint_recommendation(selected)
        await q.edit_message_text(
            "Отлично. Вот рекомендации по выбранному:\n"
            f"{tips}\n\n"
            "Важно, чтобы было строго по закону?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Да, строго по закону", callback_data="tlegal:yes"),
                        InlineKeyboardButton("Главное комфорт/вид", callback_data="tlegal:no"),
                    ]
                ]
            ),
        )
        return TINT_LEGAL

    if data.startswith("tint:"):
        val = data.split("tint:", 1)[1]
        if val in selected:
            selected.remove(val)
        else:
            selected.add(val)
        await q.edit_message_reply_markup(reply_markup=tint_keyboard(selected))
        return TINT_GLASS_MULTI

    return TINT_GLASS_MULTI

async def tint_legal_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "tlegal:yes":
        context.user_data["tint_legal"] = "Да"
        hint = "Ок 👍 Тогда предложим варианты плёнки с упором на законность/видимость."
    elif data == "tlegal:no":
        context.user_data["tint_legal"] = "Не принципиально"
        hint = "Понял 👍 Тогда подберём комфорт/приватность, расскажем плюсы/минусы."
    else:
        return TINT_LEGAL

    await q.edit_message_text(
        f"{hint}\n\nЧто для тебя важнее?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Приватность", callback_data="tprio:privacy"),
                    InlineKeyboardButton("Комфорт/жара", callback_data="tprio:comfort"),
                ],
                [
                    InlineKeyboardButton("Внешний вид", callback_data="tprio:look"),
                    InlineKeyboardButton("Не знаю, подберите", callback_data="tprio:help"),
                ],
            ]
        ),
    )
    return TINT_PRIORITY

async def tint_priority_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    mapping = {
        "tprio:privacy": "Приватность",
        "tprio:comfort": "Комфорт/жара",
        "tprio:look": "Внешний вид",
        "tprio:help": "Подберите",
    }
    if data not in mapping:
        return TINT_PRIORITY

    context.user_data["tint_priority"] = mapping[data]

    # ветка "Тонировка" завершена — убираем её из очереди
    queue = context.user_data.get("branch_queue", [])
    if queue and queue[0] == "Тонировка":
        queue.pop(0)
        context.user_data["branch_queue"] = queue

    await q.edit_message_text(
        "Отлично, принял ✅\n"
        "Дальше уточню время и контакт, чтобы записать тебя.",
        reply_markup=None,
    )
    return await run_next_branch(update, context)

# -------- TIME --------
async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kb = ReplyKeyboardMarkup(
        keyboard=[
            ["Сегодня", "Завтра"],
            ["На этой неделе", "На выходных"],
            ["Пока не знаю"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(
            "Когда удобно приехать? (можно написать текстом: «среда после 18:00»)",
            reply_markup=kb,
        )
    else:
        await update.message.reply_text(
            "Когда удобно приехать? (можно написать текстом: «среда после 18:00»)",
            reply_markup=kb,
        )
    return ASK_TIME

async def got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = (update.message.text or "").strip()
    context.user_data["time_pref"] = txt

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
        "Чтобы подтвердить запись и уточнить детали — оставь контакт:",
        reply_markup=kb,
    )
    return ASK_CONTACT

# -------- CONTACT + SEND LEAD TO OWNER --------
async def got_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        context.user_data["phone"] = phone
        context.user_data["contact_method"] = "phone"
    else:
        txt = (update.message.text or "").strip()

        if "телег" in txt.lower() or "сюда" in txt.lower() or "tg" in txt.lower():
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

    # сформировать лид
    lead_text = build_lead_text(context, update)

    # отправить владельцу
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=lead_text,
        )
    except Exception as e:
        log.exception("Failed to send lead to owner: %s", e)

    # ответ клиенту
    await update.message.reply_text(
        "✅ Принято! Я передал информацию.\n"
        "Скоро напишем/позвоним и подтвердим запись.\n\n"
        "Если хочешь — можешь дописать детали (фото/видео тоже можно).",
        reply_markup=None,
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END

# ===================== MAIN =====================
def main() -> None:
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],

            SELECT_SERVICES: [CallbackQueryHandler(services_click, pattern=r"^svc:")],

            TINT_GLASS_MULTI: [CallbackQueryHandler(tint_click, pattern=r"^tint:")],
            TINT_LEGAL: [CallbackQueryHandler(tint_legal_click, pattern=r"^tlegal:")],
            TINT_PRIORITY: [CallbackQueryHandler(tint_priority_click, pattern=r"^tprio:")],

            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_time)],

            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, got_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_contact),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()