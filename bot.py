import os
import re
import asyncio
from typing import Optional

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
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ===================== ENV =====================
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

ADMIN_ID = int(os.getenv("ADMIN_ID", "327140660"))  # твой ID по умолчанию

# ===================== SERVICES =====================
S_TINT = "tint"
S_POLISH = "polish"
S_CERAMIC = "ceramic"
S_WATER = "waterstone"
S_ANTIRAIN = "antirain"
S_HEADLIGHT = "headlight"
S_GLASS = "glasspolish"

SERVICE_LABELS = {
    S_TINT: "Тонировка",
    S_POLISH: "Полировка кузова",
    S_CERAMIC: "Керамика (защита)",
    S_WATER: "Удаление водного камня (стёкла)",
    S_ANTIRAIN: "Антидождь",
    S_HEADLIGHT: "Полировка фар",
    S_GLASS: "Шлифовка/полировка стекла",
}

SERVICE_ORDER = [
    S_TINT,
    S_POLISH,
    S_CERAMIC,
    S_WATER,
    S_ANTIRAIN,
    S_HEADLIGHT,
    S_GLASS,
]

# ===================== STATES =====================
# ВАЖНО: range(18) — с запасом, чтобы не ловить "too many values to unpack"
(
    ASK_NAME,
    SELECT_SERVICES,

    # tint
    TINT_GLASS_MULTI,
    TINT_LEGAL,
    TINT_PRIORITY,

    # polish
    POLISH_COND_MULTI,
    POLISH_AGE,

    # ceramic
    CERAMIC_POLISHED,
    CERAMIC_GOAL,

    # waterstone
    WATER_ZONE_MULTI,

    # antirain
    ANTIRAIN_ZONE,

    # headlight
    HEADLIGHT_STATE,

    # glass polish
    GLASS_WIPER,

    # finish
    ASK_TIME,
    ASK_CONTACT,
) = range(18)

# ===================== HELPERS =====================
def normalize_phone(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)
    only_digits = re.sub(r"\D", "", digits)

    if len(only_digits) < 10:
        return None

    if digits.startswith("8") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]
    if digits.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]

    if digits.startswith("+") and len(only_digits) >= 11:
        return digits

    return None


def ud_init(context: ContextTypes.DEFAULT_TYPE) -> None:
    if "details" not in context.user_data:
        context.user_data["details"] = {}
    if "services_selected" not in context.user_data:
        context.user_data["services_selected"] = set()
    if "services_queue" not in context.user_data:
        context.user_data["services_queue"] = []
    if "service_index" not in context.user_data:
        context.user_data["service_index"] = 0


def kb_services(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for code in SERVICE_ORDER:
        label = SERVICE_LABELS[code]
        checked = "✅" if code in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{checked} {label}", callback_data=f"svc:{code}")])

    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc_done"),
            InlineKeyboardButton("Сбросить ↩", callback_data="svc_reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def kb_multi(title_to_code: dict[str, str], selected_codes: set[str], done_cb: str, reset_cb: str) -> InlineKeyboardMarkup:
    rows = []
    for title, code in title_to_code.items():
        checked = "✅" if code in selected_codes else "⬜"
        rows.append([InlineKeyboardButton(f"{checked} {title}", callback_data=f"m:{code}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data=done_cb),
            InlineKeyboardButton("Сбросить ↩", callback_data=reset_cb),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def send_admin_lead(app: Application, lead_text: str) -> None:
    try:
        await app.bot.send_message(chat_id=ADMIN_ID, text=lead_text)
    except Exception as e:
        print(f"[ADMIN SEND ERROR] {e}")


def current_service(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    q = context.user_data.get("services_queue", [])
    i = context.user_data.get("service_index", 0)
    if 0 <= i < len(q):
        return q[i]
    return None


async def go_next_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    srv = current_service(context)

    if srv is None:
        await update.effective_message.reply_text(
            "Отлично 👍 Теперь подберём время.\nКогда удобно записаться?",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    ["Сегодня", "Завтра"],
                    ["В выходные", "Напишу время сам"],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return ASK_TIME

    if srv == S_TINT:
        context.user_data["tint_glass_selected"] = set()
        await update.effective_message.reply_text(
            "🪟 *Тонировка*\nВыбери стёкла (можно несколько) и нажми «Готово ✅».",
            parse_mode="Markdown",
            reply_markup=kb_multi(
                title_to_code={
                    "Полусфера зад": "rear_half",
                    "Боковые зад": "rear_sides",
                    "Боковые перед": "front_sides",
                    "Лобовое": "windshield",
                    "Заднее стекло": "rear_window",
                },
                selected_codes=context.user_data["tint_glass_selected"],
                done_cb="tint_glass_done",
                reset_cb="tint_glass_reset",
            ),
        )
        return TINT_GLASS_MULTI

    if srv == S_POLISH:
        context.user_data["polish_cond_selected"] = set()
        await update.effective_message.reply_text(
            "✨ *Полировка кузова*\nКак сейчас выглядит кузов? (можно выбрать 1–2) и нажми «Готово ✅».",
            parse_mode="Markdown",
            reply_markup=kb_multi(
                title_to_code={
                    "Потускнел / нет блеска": "dull",
                    "Есть мелкие царапины": "scratches",
                    "После моек / автоматов": "washes",
                    "Хочу освежить внешний вид": "refresh",
                },
                selected_codes=context.user_data["polish_cond_selected"],
                done_cb="polish_cond_done",
                reset_cb="polish_cond_reset",
            ),
        )
        return POLISH_COND_MULTI

    if srv == S_CERAMIC:
        await update.effective_message.reply_text(
            "🛡 *Керамика*\nДелали ли полировку ранее?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["Да"], ["Нет"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return CERAMIC_POLISHED

    if srv == S_WATER:
        context.user_data["water_zone_selected"] = set()
        await update.effective_message.reply_text(
            "💧 *Удаление водного камня*\nГде сильнее всего налёт? (можно несколько) и нажми «Готово ✅».",
            parse_mode="Markdown",
            reply_markup=kb_multi(
                title_to_code={
                    "Лобовое": "windshield",
                    "Боковые": "sides",
                    "Заднее": "rear",
                },
                selected_codes=context.user_data["water_zone_selected"],
                done_cb="water_zone_done",
                reset_cb="water_zone_reset",
            ),
        )
        return WATER_ZONE_MULTI

    if srv == S_ANTIRAIN:
        await update.effective_message.reply_text(
            "🌧 *Антидождь*\nКуда нанести?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["Лобовое"], ["Все стёкла"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return ANTIRAIN_ZONE

    if srv == S_HEADLIGHT:
        await update.effective_message.reply_text(
            "💡 *Полировка фар*\nФары мутные/желтят?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["Да"], ["Немного"], ["Хочу профилактику"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return HEADLIGHT_STATE

    if srv == S_GLASS:
        await update.effective_message.reply_text(
            "🧊 *Полировка/шлифовка стекла*\nЕсть царапины от дворников?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["Да"], ["Немного"], ["Не уверен"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return GLASS_WIPER

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ===================== RENDER PORT "KOSTYL" =====================
async def _http_handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        await reader.read(1024)
        resp = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n\r\nOK"
        )
        writer.write(resp)
        await writer.drain()
    except Exception:
        pass
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def start_port_server():
    port = int(os.getenv("PORT", "10000"))
    server = await asyncio.start_server(_http_handle, host="0.0.0.0", port=port)
    print(f"[PORT SERVER] listening on 0.0.0.0:{port}")
    return server


async def post_init(app: Application):
    try:
        srv = await start_port_server()
        app.bot_data["port_server"] = srv
    except Exception as e:
        print(f"[PORT SERVER ERROR] {e}")


# ===================== HANDLERS =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    ud_init(context)

    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя.\n\nКак тебя зовут?"
    )
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    context.user_data["services_selected"] = set()
    context.user_data["services_queue"] = []
    context.user_data["service_index"] = 0
    context.user_data["details"] = {}

    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=kb_services(context.user_data["services_selected"]),
    )
    return SELECT_SERVICES


async def services_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    selected: set[str] = context.user_data["services_selected"]

    if data.startswith("svc:"):
        code = data.split(":", 1)[1]
        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)
        await q.edit_message_reply_markup(reply_markup=kb_services(selected))
        return SELECT_SERVICES

    if data == "svc_reset":
        selected.clear()
        await q.edit_message_reply_markup(reply_markup=kb_services(selected))
        return SELECT_SERVICES

    if data == "svc_done":
        if not selected:
            await q.answer("Выбери хотя бы одну услугу 🙂", show_alert=True)
            return SELECT_SERVICES

        queue = [c for c in SERVICE_ORDER if c in selected]
        context.user_data["services_queue"] = queue
        context.user_data["service_index"] = 0

        nice = ", ".join(SERVICE_LABELS[c] for c in queue)
        await q.edit_message_text(
            f"Отлично 👍 Выбрано: *{nice}*\nДавай уточним детали 👇",
            parse_mode="Markdown",
        )
        return await go_next_service(update, context)

    return SELECT_SERVICES


# ---------- TINT ----------
async def tint_glass_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    selected: set[str] = context.user_data.get("tint_glass_selected", set())

    mapping = {
        "rear_half": "Полусфера зад",
        "rear_sides": "Боковые зад",
        "front_sides": "Боковые перед",
        "windshield": "Лобовое",
        "rear_window": "Заднее стекло",
    }

    if data.startswith("m:"):
        code = data.split(":", 1)[1]
        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)
        context.user_data["tint_glass_selected"] = selected

        await q.edit_message_reply_markup(
            reply_markup=kb_multi(
                title_to_code={v: k for k, v in mapping.items()},
                selected_codes=selected,
                done_cb="tint_glass_done",
                reset_cb="tint_glass_reset",
            )
        )
        return TINT_GLASS_MULTI

    if data == "tint_glass_reset":
        selected.clear()
        context.user_data["tint_glass_selected"] = selected
        await q.edit_message_reply_markup(
            reply_markup=kb_multi(
                title_to_code={v: k for k, v in mapping.items()},
                selected_codes=selected,
                done_cb="tint_glass_done",
                reset_cb="tint_glass_reset",
            )
        )
        return TINT_GLASS_MULTI

    if data == "tint_glass_done":
        if not selected:
            await q.answer("Выбери хотя бы одно стекло 🙂", show_alert=True)
            return TINT_GLASS_MULTI

        glass_titles = [mapping[c] for c in mapping if c in selected]
        context.user_data["details"].setdefault(S_TINT, {})
        context.user_data["details"][S_TINT]["glass"] = glass_titles

        rec_parts = []
        if "rear_half" in selected:
            rec_parts.append("• Задняя полусфера — популярный вариант: меньше нагрев, больше комфорта.")
        if "windshield" in selected or "front_sides" in selected:
            rec_parts.append("• Для лобового/передних боковых можно подобрать вариант «по ГОСТ», чтобы было спокойно.")

        text = "🪟 *Тонировка*\nВыбрано: " + ", ".join(glass_titles)
        if rec_parts:
            text += "\n\n*Подсказка:*\n" + "\n".join(rec_parts)

        await q.edit_message_text(text, parse_mode="Markdown")

        if ("windshield" in selected) or ("front_sides" in selected):
            await update.effective_message.reply_text(
                "Нужна *легальная тонировка* (по ГОСТ) или *потемнее*?",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[["Да, по ГОСТ ✅"], ["Нет, потемнее 😎"]],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
            )
            return TINT_LEGAL

        await update.effective_message.reply_text(
            "Что важнее?",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["Комфорт и тепло"], ["Приватность"], ["Максимально тёмно"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return TINT_PRIORITY

    return TINT_GLASS_MULTI


async def tint_legal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip().lower()

    if "гост" in txt or "да" in txt:
        legal = "ГОСТ"
    elif "нет" in txt or "темн" in txt:
        legal = "Потемнее"
    else:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return TINT_LEGAL

    context.user_data["details"].setdefault(S_TINT, {})
    context.user_data["details"][S_TINT]["legal"] = legal

    if legal == "ГОСТ":
        await update.message.reply_text("Ок ✅ Подберём плёнку с высокой светопропускаемостью — без вопросов.")
    else:
        await update.message.reply_text("Понял 😎 Подберём вариант потемнее под стиль и комфорт.")

    await update.message.reply_text(
        "Что важнее?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[["Комфорт и тепло"], ["Приватность"], ["Максимально тёмно"]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return TINT_PRIORITY


async def tint_priority(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()

    allowed = {"Комфорт и тепло", "Приватность", "Максимально тёмно"}
    if txt not in allowed:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return TINT_PRIORITY

    context.user_data["details"].setdefault(S_TINT, {})
    context.user_data["details"][S_TINT]["priority"] = txt

    if txt == "Комфорт и тепло":
        msg = "Отлично 👍 Тогда приоритет — плёнка, которая лучше держит тепло."
    elif txt == "Приватность":
        msg = "Понял 👍 Сделаем акцент на приватность."
    else:
        msg = "Ок 😎 Подберём максимально тёмный вариант под выбранные стёкла."

    await update.message.reply_text(msg)

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ---------- POLISH ----------
async def polish_cond_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    selected: set[str] = context.user_data.get("polish_cond_selected", set())

    mapping = {
        "dull": "Потускнел / нет блеска",
        "scratches": "Есть мелкие царапины",
        "washes": "После моек / автоматов",
        "refresh": "Хочу освежить внешний вид",
    }

    if data.startswith("m:"):
        code = data.split(":", 1)[1]
        if code in selected:
            selected.remove(code)
        else:
            if len(selected) >= 2:
                await q.answer("Можно выбрать максимум 2 пункта 🙂", show_alert=True)
                return POLISH_COND_MULTI
            selected.add(code)

        context.user_data["polish_cond_selected"] = selected

        await q.edit_message_reply_markup(
            reply_markup=kb_multi(
                title_to_code={v: k for k, v in mapping.items()},
                selected_codes=selected,
                done_cb="polish_cond_done",
                reset_cb="polish_cond_reset",
            )
        )
        return POLISH_COND_MULTI

    if data == "polish_cond_reset":
        selected.clear()
        context.user_data["polish_cond_selected"] = selected
        await q.edit_message_reply_markup(
            reply_markup=kb_multi(
                title_to_code={v: k for k, v in mapping.items()},
                selected_codes=selected,
                done_cb="polish_cond_done",
                reset_cb="polish_cond_reset",
            )
        )
        return POLISH_COND_MULTI

    if data == "polish_cond_done":
        if not selected:
            await q.answer("Выбери хотя бы 1 пункт 🙂", show_alert=True)
            return POLISH_COND_MULTI

        picked = [mapping[c] for c in mapping if c in selected]
        context.user_data["details"].setdefault(S_POLISH, {})
        context.user_data["details"][S_POLISH]["condition"] = picked

        await q.edit_message_text(
            "✨ *Полировка кузова*\nПонял: " + ", ".join(picked) + "\n\nМашина новая или уже не первый год?",
            parse_mode="Markdown",
        )

        await update.effective_message.reply_text(
            "Выбери возраст машины:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["До 3 лет"], ["3–7 лет"], ["Более 7 лет"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return POLISH_AGE

    return POLISH_COND_MULTI


async def polish_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()
    allowed = {"До 3 лет", "3–7 лет", "Более 7 лет"}
    if txt not in allowed:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return POLISH_AGE

    context.user_data["details"].setdefault(S_POLISH, {})
    context.user_data["details"][S_POLISH]["age"] = txt

    await update.message.reply_text(
        "Рекомендация ✅ Обычно лучше всего подходит восстановительная полировка — цвет становится глубже."
    )

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ---------- CERAMIC ----------
async def ceramic_polished(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()
    if txt not in {"Да", "Нет"}:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return CERAMIC_POLISHED

    context.user_data["details"].setdefault(S_CERAMIC, {})
    context.user_data["details"][S_CERAMIC]["polished_before"] = txt

    await update.message.reply_text(
        "Для чего защита важнее?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[["Блеск"], ["Защита от грязи и реагентов"], ["Облегчить мойку"]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return CERAMIC_GOAL


async def ceramic_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()
    allowed = {"Блеск", "Защита от грязи и реагентов", "Облегчить мойку"}
    if txt not in allowed:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return CERAMIC_GOAL

    context.user_data["details"].setdefault(S_CERAMIC, {})
    context.user_data["details"][S_CERAMIC]["goal"] = txt

    polished_before = context.user_data["details"][S_CERAMIC].get("polished_before", "Нет")
    if polished_before == "Нет":
        rec = "Керамика лучше всего работает после полировки — эффект держится дольше."
    else:
        rec = "Если полировка была недавно — керамика ляжет идеально и будет держаться дольше."
    await update.message.reply_text(f"Рекомендация ✅ {rec}")

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ---------- WATERSTONE ----------
async def water_zone_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    selected: set[str] = context.user_data.get("water_zone_selected", set())

    mapping = {
        "windshield": "Лобовое",
        "sides": "Боковые",
        "rear": "Заднее",
    }

    if data.startswith("m:"):
        code = data.split(":", 1)[1]
        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)
        context.user_data["water_zone_selected"] = selected

        await q.edit_message_reply_markup(
            reply_markup=kb_multi(
                title_to_code={v: k for k, v in mapping.items()},
                selected_codes=selected,
                done_cb="water_zone_done",
                reset_cb="water_zone_reset",
            )
        )
        return WATER_ZONE_MULTI

    if data == "water_zone_reset":
        selected.clear()
        context.user_data["water_zone_selected"] = selected
        await q.edit_message_reply_markup(
            reply_markup=kb_multi(
                title_to_code={v: k for k, v in mapping.items()},
                selected_codes=selected,
                done_cb="water_zone_done",
                reset_cb="water_zone_reset",
            )
        )
        return WATER_ZONE_MULTI

    if data == "water_zone_done":
        if not selected:
            await q.answer("Выбери хотя бы 1 вариант 🙂", show_alert=True)
            return WATER_ZONE_MULTI

        picked = [mapping[c] for c in mapping if c in selected]
        context.user_data["details"].setdefault(S_WATER, {})
        context.user_data["details"][S_WATER]["zones"] = picked

        await q.edit_message_text(
            "💧 *Удаление водного камня*\nПонял: " + ", ".join(picked) + "\n\n"
            "Подсказка: после удаления налёта часто рекомендуем *антидождь* — эффект держится дольше.",
            parse_mode="Markdown",
        )

        context.user_data["service_index"] += 1
        return await go_next_service(update, context)

    return WATER_ZONE_MULTI


# ---------- ANTIRAIN ----------
async def antirain_zone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()
    if txt not in {"Лобовое", "Все стёкла"}:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return ANTIRAIN_ZONE

    context.user_data["details"].setdefault(S_ANTIRAIN, {})
    context.user_data["details"][S_ANTIRAIN]["zone"] = txt

    await update.message.reply_text("Рекомендация ✅ На лобовом эффект максимальный — вода уходит уже с 60–70 км/ч.")

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ---------- HEADLIGHT ----------
async def headlight_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()
    allowed = {"Да", "Немного", "Хочу профилактику"}
    if txt not in allowed:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return HEADLIGHT_STATE

    context.user_data["details"].setdefault(S_HEADLIGHT, {})
    context.user_data["details"][S_HEADLIGHT]["state"] = txt

    await update.message.reply_text("Рекомендация ✅ После полировки свет становится ярче, а внешний вид — свежее.")

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ---------- GLASS POLISH ----------
async def glass_wiper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()
    allowed = {"Да", "Немного", "Не уверен"}
    if txt not in allowed:
        await update.message.reply_text("Выбери кнопкой 🙂")
        return GLASS_WIPER

    context.user_data["details"].setdefault(S_GLASS, {})
    context.user_data["details"][S_GLASS]["wiper_scratches"] = txt

    await update.message.reply_text("Рекомендация ✅ Если царапины неглубокие — можно восстановить без замены стекла.")

    context.user_data["service_index"] += 1
    return await go_next_service(update, context)


# ---------- TIME ----------
async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)
    txt = (update.message.text or "").strip()

    if txt == "Напишу время сам":
        await update.message.reply_text("Ок 🙂 Напиши удобное время (например: «завтра после 18:00»).")
        return ASK_TIME

    if txt in {"Сегодня", "Завтра", "В выходные"}:
        context.user_data["time_pref"] = txt
    else:
        if len(txt) < 2:
            await update.message.reply_text("Напиши время чуть понятнее 🙂")
            return ASK_TIME
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
        "Спасибо! Теперь оставь удобный контакт:\n"
        "• нажми «Отправить контакт ☎️»\n"
        "• или напиши номер текстом\n"
        "• или просто скажи «можно сюда в Telegram»",
        reply_markup=kb,
    )
    return ASK_CONTACT


# ---------- CONTACT + LEAD ----------
async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ud_init(context)

    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        context.user_data["contact_method"] = "phone"
        context.user_data["phone"] = phone
    else:
        txt = (update.message.text or "").strip()

        if txt == "Написать номер текстом":
            await update.message.reply_text("Ок 🙂 Напиши номер в формате +7... или 8...")
            return ASK_CONTACT

        if "телег" in txt.lower() or "сюда" in txt.lower() or "tg" in txt.lower():
            context.user_data["contact_method"] = "telegram"
            context.user_data["phone"] = ""
        else:
            phone = normalize_phone(txt)
            if not phone:
                await update.message.reply_text(
                    "Не похоже на номер 🙂\nНапиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
                )
                return ASK_CONTACT
            context.user_data["contact_method"] = "phone"
            context.user_data["phone"] = phone

    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "(нет username)"

    services = context.user_data.get("services_queue", [])
    services_txt = ", ".join(SERVICE_LABELS.get(s, s) for s in services) if services else "-"

    details = context.user_data.get("details", {})
    time_pref = context.user_data.get("time_pref", "-")
    contact_str = context.user_data.get("phone") or "Telegram"

    lead_lines = [
        "🔥 НОВЫЙ ЛИД (RKS studio)",
        f"Имя: {context.user_data.get('name', '-')}",
        f"TG: {username}",
        f"Услуги: {services_txt}",
        f"Время: {time_pref}",
        f"Контакт: {contact_str}",
        "",
        "— Детали —",
    ]

    for srv in services:
        srv_label = SERVICE_LABELS.get(srv, srv)
        srv_data = details.get(srv, {})
        lead_lines.append(f"* {srv_label}:")
        if not srv_data:
            lead_lines.append("  - (нет деталей)")
            continue
        for k, v in srv_data.items():
            v_str = ", ".join(v) if isinstance(v, list) else str(v)
            lead_lines.append(f"  - {k}: {v_str}")

    lead_text = "\n".join(lead_lines)

    await send_admin_lead(context.application, lead_text)

    await update.message.reply_text(
        "✅ Принято! Я отправил заявку менеджеру.\n"
        "Если хочешь — можешь дописать детали (фото/видео тоже можно).",
        reply_markup=None,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


# ===================== MAIN =====================
def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            SELECT_SERVICES: [CallbackQueryHandler(services_click)],

            TINT_GLASS_MULTI: [CallbackQueryHandler(tint_glass_click)],
            TINT_LEGAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, tint_legal)],
            TINT_PRIORITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, tint_priority)],

            POLISH_COND_MULTI: [CallbackQueryHandler(polish_cond_click)],
            POLISH_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, polish_age)],

            CERAMIC_POLISHED: [MessageHandler(filters.TEXT & ~filters.COMMAND, ceramic_polished)],
            CERAMIC_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ceramic_goal)],

            WATER_ZONE_MULTI: [CallbackQueryHandler(water_zone_click)],

            ANTIRAIN_ZONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, antirain_zone)],

            HEADLIGHT_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, headlight_state)],

            GLASS_WIPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, glass_wiper)],

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