import os
import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------- env -----------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").strip()
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg").strip() or "/tg"
if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = "/" + WEBHOOK_PATH

OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "327140660"))

PORT = int(os.getenv("PORT", "10000"))

# ----------------- logging -----------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("rks_bot")

# ----------------- states -----------------
ASK_NAME, CHOOSE_SERVICES, SERVICE_FLOW, ASK_TIME, ASK_CONTACT = range(5)

# ----------------- helpers -----------------
def normalize_phone(s: str) -> Optional[str]:
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
    if digits.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]
    if digits.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]

    # если уже в + и похоже на номер — оставим как есть
    if digits.startswith("+") and len(only_digits) >= 10:
        return digits

    # иначе просто вернем очищенное
    return digits if len(only_digits) >= 10 else None


def safe_username(update: Update) -> str:
    u = update.effective_user
    if not u:
        return "(нет user)"
    return f"@{u.username}" if u.username else "(нет username)"


def order_services(selected: List[str]) -> List[str]:
    # фиксированный порядок, чтобы логика шла одинаково
    order = [
        "tint",
        "body_polish",
        "ceramic",
        "water_spots",
        "anti_rain",
        "headlights",
        "glass_polish",
    ]
    sset = set(selected)
    return [x for x in order if x in sset]


# ----------------- catalog -----------------
SERVICES: Dict[str, str] = {
    "tint": "Тонировка",
    "body_polish": "Полировка кузова",
    "ceramic": "Керамика (защита)",
    "water_spots": "Удаление водного камня (стёкла)",
    "anti_rain": "Антидождь",
    "headlights": "Полировка фар",
    "glass_polish": "Шлифовка/полировка стекла",
}

# callback data
# choose services: svc|toggle|<key>  / svc|done / svc|reset
# flow: flow|toggle|<opt> / flow|done / flow|pick|<opt>

# ----------------- flow definitions -----------------
@dataclass
class Step:
    kind: str  # "single" | "multi" | "text"
    title: str
    options: Optional[List[Tuple[str, str]]] = None  # (id, label)
    hint: Optional[str] = None


SERVICE_STEPS: Dict[str, List[Step]] = {
    "tint": [
        Step(
            kind="multi",
            title="Тонировка: что именно тонируем? (можно несколько)",
            options=[
                ("rear_hemi", "Полусфера зад"),
                ("front_sides", "Боковые перед"),
                ("rear_sides", "Боковые зад"),
                ("windshield", "Лобовое"),
                ("rear_window", "Заднее стекло"),
            ],
            hint="Совет: для комфорта и приватности чаще выбирают «полусфера зад».",
        ),
        Step(
            kind="single",
            title="Тонировка: какая темнота нужна?",
            options=[
                ("5", "5% (очень темно)"),
                ("15", "15% (комфортно)"),
                ("35", "35% (умеренно)"),
                ("50", "50% (лёгкая)"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Подскажем вариант под задачи: ночь/город/трасса/парковка.",
        ),
    ],
    "body_polish": [
        Step(
            kind="single",
            title="Полировка кузова: какая цель?",
            options=[
                ("light", "Убрать «паутинку»/матовость (лёгкая)"),
                ("deep", "Убрать больше царапин (глубокая)"),
                ("prep", "Под керамику/защиту"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Совет: перед керамикой почти всегда делаем подготовку/полировку.",
        )
    ],
    "ceramic": [
        Step(
            kind="single",
            title="Керамика: какой уровень защиты?",
            options=[
                ("1", "1 слой (базовая защита)"),
                ("2", "2 слоя (лучше блеск/стойкость)"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Совет: максимальный эффект — на подготовленном лаке (полировка).",
        )
    ],
    "water_spots": [
        Step(
            kind="single",
            title="Водный камень на стеклах: как сильно?",
            options=[
                ("light", "Лёгкий налёт/разводы"),
                ("medium", "Заметный налёт, плохо уходит"),
                ("hard", "Очень сильный, «белесые» точки"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Совет: после удаления — лучше закрепить антидождём, чтобы дольше держалось.",
        )
    ],
    "anti_rain": [
        Step(
            kind="single",
            title="Антидождь: куда наносим?",
            options=[
                ("windshield", "Лобовое"),
                ("front", "Лобовое + передние боковые"),
                ("all", "Все стёкла"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Совет: самый популярный вариант — лобовое + передние боковые.",
        )
    ],
    "headlights": [
        Step(
            kind="single",
            title="Фары: какая проблема?",
            options=[
                ("dull", "Мутные/потеряли прозрачность"),
                ("yellow", "Пожелтели"),
                ("scratches", "Царапины"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Совет: после полировки можно защитить покрытием, чтобы не мутнели быстрее.",
        )
    ],
    "glass_polish": [
        Step(
            kind="single",
            title="Стекло: что именно беспокоит?",
            options=[
                ("wipers", "Следы от дворников"),
                ("light", "Лёгкие царапины"),
                ("deep", "Глубокие царапины/сколы (нужно оценить)"),
                ("idk", "Не знаю — посоветуйте"),
            ],
            hint="Совет: глубокие повреждения иногда не убрать полностью — предупредим честно после осмотра.",
        )
    ],
}


def build_services_keyboard(selected: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, label in SERVICES.items():
        mark = "✅" if key in selected else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"svc|toggle|{key}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc|done"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="svc|reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_multi_keyboard(selected_ids: List[str], options: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = []
    for oid, label in options:
        mark = "✅" if oid in selected_ids else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"flow|toggle|{oid}")])
    rows.append([InlineKeyboardButton("Готово ✅", callback_data="flow|done")])
    return InlineKeyboardMarkup(rows)


def build_single_keyboard(options: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = []
    for oid, label in options:
        rows.append([InlineKeyboardButton(label, callback_data=f"flow|pick|{oid}")])
    return InlineKeyboardMarkup(rows)


def service_recommendation(service_key: str, answers: Dict[str, Any]) -> str:
    # короткие рекомендации “по ходу”
    if service_key == "tint":
        parts = answers.get("parts", [])
        if "windshield" in parts:
            return "💡 По лобовому: подберём вариант, чтобы было комфортно и без лишних вопросов по нормам."
        return "💡 По тонировке: подберём плёнку под задачи (приватность/комфорт/ночь)."

    if service_key == "water_spots":
        return "💡 Чтобы налёт не возвращался быстро — часто делают «антидождь» после очистки."

    if service_key == "ceramic":
        return "💡 Максимальный эффект керамики — на подготовленном кузове (полировка/обезжиривание)."

    if service_key == "body_polish":
        return "💡 Если планируешь керамику — лучше сразу сделать подготовку, будет заметно круче."

    if service_key == "anti_rain":
        return "💡 Эффект сильнее всего ощущается на лобовом: вода уходит быстрее, меньше напряга в дождь."

    if service_key == "headlights":
        return "💡 После полировки можем защитить фары, чтобы прозрачность держалась дольше."

    if service_key == "glass_polish":
        return "💡 Глубокие повреждения оценим по месту — скажем честно, что реально убрать."

    return ""


# ----------------- conversation handlers -----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
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
    context.user_data["selected_services"] = []
    kb = build_services_keyboard(context.user_data["selected_services"])

    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=kb,
    )
    return CHOOSE_SERVICES


async def services_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = (q.data or "").split("|")
    selected: List[str] = context.user_data.get("selected_services", [])

    if data[:2] == ["svc", "toggle"] and len(data) == 3:
        key = data[2]
        if key in SERVICES:
            if key in selected:
                selected.remove(key)
            else:
                selected.append(key)
        context.user_data["selected_services"] = selected
        await q.edit_message_reply_markup(reply_markup=build_services_keyboard(selected))
        return CHOOSE_SERVICES

    if data == ["svc", "reset"]:
        context.user_data["selected_services"] = []
        await q.edit_message_reply_markup(reply_markup=build_services_keyboard([]))
        return CHOOSE_SERVICES

    if data == ["svc", "done"]:
        if not selected:
            await q.edit_message_text("Выбери хотя бы одну услугу 🙂", reply_markup=build_services_keyboard([]))
            return CHOOSE_SERVICES

        # подготовим очередь услуг и старт первого сервиса
        queue = order_services(selected)
        context.user_data["service_queue"] = queue
        context.user_data["service_index"] = 0
        context.user_data["service_answers"] = {}  # service_key -> answers dict

        await q.edit_message_text("Отлично! Уточню пару моментов по выбранным услугам 👇")
        return await start_next_service(update, context)

    return CHOOSE_SERVICES


async def start_next_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = int(context.user_data.get("service_index", 0))
    queue: List[str] = context.user_data.get("service_queue", [])
    if idx >= len(queue):
        # все услуги уточнили
        await update.effective_chat.send_message(
            "Когда тебе удобно подъехать? Напиши день/время (например: «сегодня после 18:00» или «в пятницу 12:00»)."
        )
        return ASK_TIME

    service_key = queue[idx]
    context.user_data["current_service"] = service_key
    context.user_data["current_step"] = 0

    # init answers for this service
    all_answers: Dict[str, Dict[str, Any]] = context.user_data.get("service_answers", {})
    all_answers.setdefault(service_key, {})
    context.user_data["service_answers"] = all_answers

    return await show_current_step(update, context)


async def show_current_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service_key = context.user_data["current_service"]
    step_idx = int(context.user_data.get("current_step", 0))
    steps = SERVICE_STEPS.get(service_key, [])

    if step_idx >= len(steps):
        # сервис закончен — рекомендация и к следующему
        ans = context.user_data["service_answers"][service_key]
        rec = service_recommendation(service_key, ans)
        if rec:
            await update.effective_chat.send_message(rec)

        context.user_data["service_index"] = int(context.user_data.get("service_index", 0)) + 1
        return await start_next_service(update, context)

    step = steps[step_idx]
    title = f"**{SERVICES.get(service_key, service_key)}**\n{step.title}"
    hint = f"\n\n{step.hint}" if step.hint else ""

    if step.kind == "multi":
        # answers store list
        ans = context.user_data["service_answers"][service_key]
        selected_ids = ans.get("parts", [])
        kb = build_multi_keyboard(selected_ids, step.options or [])
        await update.effective_chat.send_message(
            title + hint,
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return SERVICE_FLOW

    if step.kind == "single":
        kb = build_single_keyboard(step.options or [])
        await update.effective_chat.send_message(
            title + hint,
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return SERVICE_FLOW

    if step.kind == "text":
        await update.effective_chat.send_message(title + hint, parse_mode="Markdown")
        return SERVICE_FLOW

    await update.effective_chat.send_message("Ошибка конфигурации шага.")
    return ConversationHandler.END


async def flow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "").split("|")

    service_key = context.user_data.get("current_service")
    step_idx = int(context.user_data.get("current_step", 0))
    steps = SERVICE_STEPS.get(service_key, [])
    if step_idx >= len(steps):
        # на всякий случай
        return await start_next_service(update, context)

    step = steps[step_idx]
    ans = context.user_data["service_answers"][service_key]

    if data[:2] == ["flow", "toggle"] and step.kind == "multi" and len(data) == 3:
        oid = data[2]
        selected_ids = ans.get("parts", [])
        if oid in selected_ids:
            selected_ids.remove(oid)
        else:
            selected_ids.append(oid)
        ans["parts"] = selected_ids
        context.user_data["service_answers"][service_key] = ans

        # обновим клавиатуру
        kb = build_multi_keyboard(selected_ids, step.options or [])
        await q.edit_message_reply_markup(reply_markup=kb)
        return SERVICE_FLOW

    if data == ["flow", "done"] and step.kind == "multi":
        # не даем пройти пустым
        if not ans.get("parts"):
            await q.answer("Выбери хотя бы один пункт 🙂", show_alert=True)
            return SERVICE_FLOW

        context.user_data["current_step"] = step_idx + 1
        # удалим клавиатуру у текущего сообщения
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await show_current_step(update, context)

    if data[:2] == ["flow", "pick"] and step.kind == "single" and len(data) == 3:
        oid = data[2]
        ans[f"step_{step_idx}"] = oid
        context.user_data["service_answers"][service_key] = ans

        # небольшая рекомендация сразу после выбора (если нужна)
        rec = service_recommendation(service_key, ans)
        # уберем клавиатуру и двинемся дальше
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        context.user_data["current_step"] = step_idx + 1
        if rec and step_idx == 0:
            await update.effective_chat.send_message(rec)

        return await show_current_step(update, context)

    return SERVICE_FLOW


async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
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
        "Ок! Оставь удобный контакт:\n"
        "• нажми «Отправить контакт ☎️»\n"
        "• или напиши номер текстом\n"
        "• или скажи «можно сюда в Telegram»",
        reply_markup=kb,
    )
    return ASK_CONTACT


async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = "telegram"
    phone = ""

    if update.message.contact and update.message.contact.phone_number:
        method = "phone"
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
    else:
        txt = (update.message.text or "").strip()
        if "телег" in txt.lower() or "сюда" in txt.lower() or "tg" in txt.lower():
            method = "telegram"
            phone = ""
        else:
            p = normalize_phone(txt)
            if not p:
                await update.message.reply_text(
                    "Не похоже на номер 🙂\n"
                    "Напиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
                )
                return ASK_CONTACT
            method = "phone"
            phone = p

    context.user_data["contact_method"] = method
    context.user_data["phone"] = phone

    # сформируем лид
    name = context.user_data.get("name", "")
    username = safe_username(update)
    selected = order_services(context.user_data.get("selected_services", []))
    answers = context.user_data.get("service_answers", {})
    time_pref = context.user_data.get("time", "")

    lines = []
    lines.append("🔥 НОВЫЙ ЛИД (RKS Studio)")
    lines.append(f"Имя: {name}")
    lines.append(f"TG: {username}")
    lines.append(f"Услуги: " + ", ".join([SERVICES[s] for s in selected]))
    lines.append(f"Когда удобно: {time_pref}")
    lines.append(f"Контакт: {phone if method == 'phone' and phone else 'Telegram'}")
    lines.append("")
    lines.append("— Уточнения —")

    for sk in selected:
        lines.append(f"\n• {SERVICES[sk]}")
        a = answers.get(sk, {})
        steps = SERVICE_STEPS.get(sk, [])
        for i, step in enumerate(steps):
            if step.kind == "multi":
                parts = a.get("parts", [])
                if step.options:
                    label_map = dict(step.options)
                    pretty = [label_map.get(x, x) for x in parts]
                    lines.append("  - Выбор: " + (", ".join(pretty) if pretty else "-"))
            elif step.kind == "single":
                pick = a.get(f"step_{i}", "")
                if step.options:
                    label_map = dict(step.options)
                    lines.append("  - " + step.title + ": " + label_map.get(pick, pick))
            elif step.kind == "text":
                val = a.get(f"step_{i}", "")
                lines.append("  - " + step.title + ": " + val)

    lead_text = "\n".join(lines)

    # в лог
    log.info("\n%s", lead_text)

    # отправим тебе в TG
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead_text)
    except Exception as e:
        log.exception("Failed to send lead to OWNER_CHAT_ID: %s", e)

    await update.message.reply_text(
        "✅ Готово! Я передал информацию менеджеру.\n"
        "Если хочешь — можешь дописать детали (фото/видео тоже можно).",
        reply_markup=None,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


# ----------------- webhook init -----------------
async def post_init(app: Application):
    # Устанавливаем webhook при старте, если задан WEBHOOK_BASE_URL
    if WEBHOOK_BASE_URL:
        webhook_url = WEBHOOK_BASE_URL.rstrip("/") + WEBHOOK_PATH
        await app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        log.info("Webhook set to: %s", webhook_url)
    else:
        log.warning("WEBHOOK_BASE_URL is empty. Webhook will not be set.")


def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            CHOOSE_SERVICES: [CallbackQueryHandler(services_callback, pattern=r"^svc\|")],
            SERVICE_FLOW: [CallbackQueryHandler(flow_callback, pattern=r"^flow\|")],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(conv)

    # WEBHOOK mode for Render Web Service
    if not WEBHOOK_BASE_URL:
        raise RuntimeError("WEBHOOK_BASE_URL not set. For Render Web Service you must set it.")

    # важно: url_path без ведущего "/"
    url_path = WEBHOOK_PATH.lstrip("/")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=url_path,
        # webhook_url уже ставим в post_init, но это поле пусть будет согласовано
        webhook_url=WEBHOOK_BASE_URL.rstrip("/") + WEBHOOK_PATH,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()