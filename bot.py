import os
import re
import time
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# -------------------- ENV --------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

MANAGER_ID = int(os.getenv("MANAGER_ID", "327140660"))
PORT = int(os.getenv("PORT", "10000"))  # Render Web Service needs an open port

WORKS_CHANNEL_URL = "https://t.me/+7nQ-MkqFk_BmZTZi"

# -------------------- LOGGING --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rks_bot")

# -------------------- Render health server (port binding "костыль") --------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "service": "rks-bot"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logger.info(f"Health server listening on 0.0.0.0:{PORT}")
        server.serve_forever()
    except Exception:
        logger.exception("Health server failed")


# -------------------- STATES --------------------
(
    S_NAME,
    S_CAR,
    S_SERVICES,
    S_SVC_FLOW,
    S_TIME,
    S_CONTACT,
    S_DONE,
) = range(7)

# -------------------- SERVICES --------------------
SERVICES = [
    ("toning", "Тонировка"),
    ("body_polish", "Полировка кузова"),
    ("ceramic", "Керамика (защита)"),
    ("water_spots", "Удаление водного камня (стёкла)"),
    ("anti_rain", "Антидождь"),
    ("headlights", "Полировка фар"),
    ("glass_polish", "Шлифовка/полировка стекла"),
    ("interior", "Химчистка салона"),
    ("engine_wash", "Мойка мотора с консервацией"),
]

SERVICE_LABEL = {k: v for k, v in SERVICES}

# -------------------- HELPERS --------------------
def now_local() -> datetime:
    return datetime.now()


def clean_text(s: str) -> str:
    return (s or "").strip()


def normalize_phone(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    digits_plus = re.sub(r"[^\d+]", "", s)
    only_digits = re.sub(r"\D", "", digits_plus)

    # 10..11 digits expected for RU
    if len(only_digits) < 10:
        return None

    if digits_plus.startswith("8") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits_plus.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits_plus.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]
    if len(only_digits) == 10:
        return "+7" + only_digits

    return None


def parse_datetime_ru(s: str) -> datetime | None:
    """
    Support:
    - "сегодня 18:00"
    - "завтра 12:30"
    - "25.12 14:00" or "25.12.2025 14:00"
    - "25/12 14:00"
    - "25-12 14:00"
    """
    txt = clean_text(s).lower()
    if not txt:
        return None

    if "вчера" in txt:
        return None

    base = now_local()
    date = base.date()

    if "сегодня" in txt:
        date = base.date()
        txt = txt.replace("сегодня", "").strip()
    elif "завтра" in txt:
        date = (base + timedelta(days=1)).date()
        txt = txt.replace("завтра", "").strip()
    elif "послезавтра" in txt:
        date = (base + timedelta(days=2)).date()
        txt = txt.replace("послезавтра", "").strip()

    m_time = re.search(r"(\d{1,2})[:.](\d{2})", txt)
    if not m_time:
        return None
    hh = int(m_time.group(1))
    mm = int(m_time.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None

    m_date = re.search(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", txt)
    if m_date:
        dd = int(m_date.group(1))
        mo = int(m_date.group(2))
        yy = m_date.group(3)
        if yy:
            yy = int(yy)
            if yy < 100:
                yy += 2000
        else:
            yy = base.year
        try:
            date = datetime(yy, mo, dd).date()
        except ValueError:
            return None

    try:
        dt = datetime(date.year, date.month, date.day, hh, mm)
    except ValueError:
        return None

    # if user entered date without year and it is already past -> next year
    if not m_date or (m_date and not m_date.group(3)):
        if dt.date() < base.date():
            try:
                dt = datetime(base.year + 1, dt.month, dt.day, dt.hour, dt.minute)
            except ValueError:
                pass

    return dt


def is_future_time(dt: datetime) -> bool:
    # at least +5 minutes
    return dt > now_local() + timedelta(minutes=5)


def lead_temperature(data: dict) -> str:
    score = 0

    # contact
    if data.get("contact_method") == "phone" and data.get("phone"):
        score += 2

    # time proximity
    dt = data.get("visit_dt")
    if isinstance(dt, datetime):
        diff = dt - now_local()
        if diff <= timedelta(days=1):
            score += 2
        elif diff <= timedelta(days=3):
            score += 1

    # services weight
    selected = data.get("services_selected", [])
    for svc in selected:
        if svc in {"ceramic", "body_polish", "glass_polish", "interior"}:
            score += 2
        elif svc in {"toning", "engine_wash"}:
            score += 1
        else:
            score += 1

    if len(selected) >= 2:
        score += 1
    if len(selected) >= 3:
        score += 1

    if score >= 7:
        return "ГОРЯЧИЙ 🔥"
    if score >= 4:
        return "ТЁПЛЫЙ 🙂"
    return "ХОЛОДНЫЙ ❄️"


def services_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, label in SERVICES:
        mark = "✅ " if key in selected else "☐ "
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"svc:{key}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc_done"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="svc_reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def yes_no_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Да", callback_data=f"{prefix}:yes"),
            InlineKeyboardButton("Нет", callback_data=f"{prefix}:no"),
        ]]
    )


def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Отправить контакт ☎️", request_contact=True)],
            [KeyboardButton("Написать номер текстом")],
            [KeyboardButton("Оставлю Telegram, можно сюда")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def channel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📸 Наши работы (TG канал)", url=WORKS_CHANNEL_URL)],
            [InlineKeyboardButton("🔁 Пройти заново", callback_data="restart")],
        ]
    )


# -------------------- UPSELLS --------------------
def compute_upsells(user_data: dict) -> list[dict]:
    selected = set(user_data.get("services_selected", []))
    ans = user_data.get("services_answers", {})

    upsells: list[dict] = []

    if "body_polish" in selected and "ceramic" not in selected:
        upsells.append({
            "title": "Керамика после полировки",
            "reason": "блеск и защита держатся заметно дольше",
        })

    if "water_spots" in selected and "anti_rain" not in selected:
        upsells.append({
            "title": "Антидождь после удаления налёта",
            "reason": "вода меньше цепляется, стекло дольше чистое",
        })

    if "glass_polish" in selected and "anti_rain" not in selected:
        chips = ans.get("glass_has_chips")
        if chips != "Да":
            upsells.append({
                "title": "Антидождь после полировки стекла",
                "reason": "на отполированном стекле работает особенно эффективно",
            })

    if "interior" in selected:
        it = ans.get("interior_type")
        if it == "Чистка кожи + пропитка":
            upsells.append({
                "title": "Усиленная пропитка кожи",
                "reason": "дольше сохраняет мягкость и защищает от загрязнений",
            })

    return upsells


def format_upsells_for_client(upsells: list[dict], limit: int = 3) -> str:
    if not upsells:
        return ""
    items = upsells[:limit]
    lines = [f"• {u['title']} — {u['reason']}" for u in items]
    return "💡 Кстати, часто берут вместе (по ситуации):\n" + "\n".join(lines)


def format_upsells_for_manager(upsells: list[dict]) -> str:
    if not upsells:
        return "—"
    return "\n".join([f"• {u['title']} — {u['reason']}" for u in upsells])


# -------------------- FLOW ENGINE --------------------
def build_service_flow(selected_services: list[str]) -> list[dict]:
    flow = []
    for svc in selected_services:
        label = SERVICE_LABEL.get(svc, svc)

        if svc == "toning":
            flow.append({
                "type": "toning_areas",
                "service": svc,
                "key": "toning_areas",
                "text": (
                    f"**{label}**\n"
                    "Какие зоны нужно затонировать? (можно несколько)\n\n"
                    "Нажимай по кнопкам и укажи **Готово ✅**."
                ),
            })
            flow.append({
                "type": "toning_percent",
                "service": svc,
                "key": "toning_percent",
                "text": (
                    f"**{label}**\n"
                    "Какой процент затемнения хочешь?\n\n"
                    "Если не уверен — выбери «Не знаю», мы подскажем."
                ),
            })
            flow.append({
                "type": "yesno",
                "service": svc,
                "key": "toning_old_film",
                "text": f"**{label}**\nЕсть старая плёнка, которую нужно снять?",
                "kb_prefix": "toning_old",
            })
            flow.append({
                "type": "info",
                "service": svc,
                "key": "toning_tip",
                "text": "💡 Совет: если есть старая плёнка — лучше снимать у нас, чтобы не повредить обогрев/нити и не оставить клей.",
            })

        elif svc == "body_polish":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "body_polish_goal",
                "text": f"**{label}**\nКакая цель полировки?",
                "options": [
                    "Убрать мелкие царапины/паутинку",
                    "Вернуть блеск/глубину цвета",
                    "Подготовка под керамику",
                    "Не знаю, нужна диагностика",
                ],
            })

        elif svc == "ceramic":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "ceramic_stage",
                "text": f"**{label}**\nКерамика делается **впервые** или это **обновление**?",
                "options": ["Впервые", "Обновление (керамика уже была)", "Не знаю"],
            })
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "ceramic_need",
                "text": f"**{label}**\nЧто важнее всего от керамики?",
                "options": ["Максимальный блеск", "Защита от реагентов/грязи", "Легче мыть авто", "Не знаю, посоветуй"],
            })
            flow.append({
                "type": "info",
                "service": svc,
                "key": "ceramic_tip",
                "text": "💡 Совет: перед керамикой лучше сделать подготовку/полировку — покрытие ляжет идеально и эффект будет заметнее.",
            })

        elif svc == "water_spots":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "water_spots_where",
                "text": f"**{label}**\nНа каких стёклах налёт/водный камень сильнее?",
                "options": ["Лобовое", "Боковые", "Заднее", "Везде"],
            })

        elif svc == "anti_rain":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "anti_rain_where",
                "text": f"**{label}**\nКуда нанести антидождь?",
                "options": ["Только лобовое", "Лобовое + боковые", "Все стёкла", "Не знаю, посоветуй"],
            })

        elif svc == "headlights":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "headlights_state",
                "text": f"**{label}**\nФары мутные/желтые или просто мелкие царапины?",
                "options": ["Сильно мутные/желтые", "Есть царапины/потёртости", "Хочу профилактику", "Не знаю"],
            })

        elif svc == "glass_polish":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "glass_polish_problem",
                "text": f"**{label}**\nЧто на стекле беспокоит больше всего?",
                "options": ["Дворники оставляют следы/затиры", "Мелкие царапины", "Пескоструй/мутность", "Не знаю, нужна диагностика"],
            })
            flow.append({
                "type": "yesno",
                "service": svc,
                "key": "glass_has_chips",
                "text": f"**{label}**\nЕсть **сколы/трещины** на стекле?",
                "kb_prefix": "glass_chips",
            })
            flow.append({
                "type": "info",
                "service": svc,
                "key": "glass_chips_tip",
                "text": (
                    "⚠️ Важно: если есть **сколы/трещины**, то **шлифовка/полировка не делается** — "
                    "нужна **замена стекла**.\n"
                    "Мы можем заменить — оставьте заявку, менеджер всё подскажет."
                ),
            })

        elif svc == "interior":
            flow.append({
                "type": "choice",
                "service": svc,
                "key": "interior_type",
                "text": f"**{label}**\nЧто именно нужно по салону?",
                "options": ["Экспресс уборка", "Полная химчистка салона", "Чистка кожи + пропитка", "Не знаю, посоветуй"],
            })

        elif svc == "engine_wash":
            flow.append({
                "type": "yesno",
                "service": svc,
                "key": "engine_recent",
                "text": f"**{label}**\nМойку мотора делали ранее?",
                "kb_prefix": "engine_prev",
            })
            flow.append({
                "type": "info",
                "service": svc,
                "key": "engine_tip",
                "text": "💡 Совет: делаем аккуратно + консервация — это защищает разъёмы и резинки, моторный отсек выглядит аккуратно дольше.",
            })

    return flow


def choice_kb(prefix: str, options: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for idx, opt in enumerate(options):
        rows.append([InlineKeyboardButton(opt, callback_data=f"{prefix}:{idx}")])
    return InlineKeyboardMarkup(rows)


def toning_areas_kb(selected: set[str]) -> InlineKeyboardMarkup:
    areas = [
        ("rear_hemi", "Полусфера зад"),
        ("front_hemi", "Полусфера перед"),
        ("side_rear", "Боковые зад"),
        ("side_front", "Боковые перед"),
        ("windshield", "Лобовое"),
        ("rear_window", "Заднее стекло"),
    ]
    rows = []
    for k, label in areas:
        mark = "✅ " if k in selected else "☐ "
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"ta:{k}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="ta_done"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="ta_reset"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def toning_percent_kb() -> InlineKeyboardMarkup:
    percents = ["2%", "5%", "15%", "20%", "35%", "Не знаю"]
    rows = [[InlineKeyboardButton(p, callback_data=f"tp:{p}")] for p in percents]
    return InlineKeyboardMarkup(rows)


# -------------------- CORE HANDLERS --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя ✅\n\n"
        "Как тебя зовут?"
    )
    return S_NAME


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок! Давай заново 🙂\n\nКак тебя зовут?")
    return S_NAME


async def cb_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.message.reply_text("Ок! Давай заново 🙂\n\nКак тебя зовут?")
    return S_NAME


async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = clean_text(update.message.text)
    if len(name) < 2 or not re.search(r"[A-Za-zА-Яа-яЁё]", name):
        await update.message.reply_text("Напиши имя понятнее 🙂")
        return S_NAME

    context.user_data["name"] = name
    await update.message.reply_text(
        "Отлично 👍\n"
        "Напиши, пожалуйста: **марка, модель, год выпуска**.\n"
        "Пример: `Toyota Camry 2018`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_CAR


async def on_car(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = clean_text(update.message.text)
    if len(txt) < 4:
        await update.message.reply_text("Чуть подробнее 🙂 Например: `Toyota Camry 2018`", parse_mode=ParseMode.MARKDOWN)
        return S_CAR

    context.user_data["car"] = txt
    context.user_data["services_selected_set"] = set()

    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=services_keyboard(context.user_data["services_selected_set"]),
    )
    return S_SERVICES


async def cb_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    selected: set[str] = context.user_data.get("services_selected_set", set())
    data = q.data

    if data.startswith("svc:"):
        svc = data.split(":", 1)[1]
        if svc in selected:
            selected.remove(svc)
        else:
            selected.add(svc)
        context.user_data["services_selected_set"] = selected
        await q.edit_message_reply_markup(reply_markup=services_keyboard(selected))
        return S_SERVICES

    if data == "svc_reset":
        selected.clear()
        context.user_data["services_selected_set"] = selected
        await q.edit_message_reply_markup(reply_markup=services_keyboard(selected))
        return S_SERVICES

    if data == "svc_done":
        if not selected:
            await q.message.reply_text("Выбери хотя бы одну услугу 🙂")
            return S_SERVICES

        ordered = [k for k, _ in SERVICES if k in selected]
        context.user_data["services_selected"] = ordered
        context.user_data["services_answers"] = {}
        context.user_data["flow"] = build_service_flow(ordered)
        context.user_data["flow_i"] = 0

        await q.message.reply_text("Отлично! Уточню пару моментов по выбранным услугам 👇")
        return await ask_next_flow_step(q.message, context)

    return S_SERVICES


async def ask_next_flow_step(message, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("flow", [])
    i = context.user_data.get("flow_i", 0)

    if i >= len(flow):
        upsells = compute_upsells(context.user_data)
        tip = format_upsells_for_client(upsells, limit=3)
        if tip:
            await message.reply_text(tip)

        await message.reply_text(
            "Когда тебе удобно подъехать? Напиши **день/время**.\n"
            "Примеры:\n"
            "• `сегодня 18:00`\n"
            "• `завтра 12:00`\n"
            "• `25.12 14:00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return S_TIME

    step = flow[i]
    stype = step["type"]
    text = step["text"]

    if stype == "info":
        if step["key"] == "glass_chips_tip":
            ans = context.user_data.get("services_answers", {}).get("glass_has_chips")
            if ans != "Да":
                context.user_data["flow_i"] = i + 1
                return await ask_next_flow_step(message, context)

        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data["flow_i"] = i + 1
        return await ask_next_flow_step(message, context)

    if stype == "choice":
        kb = choice_kb(f"ch:{step['key']}", step["options"])
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return S_SVC_FLOW

    if stype == "yesno":
        kb = yes_no_kb(step["kb_prefix"])
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return S_SVC_FLOW

    if stype == "toning_areas":
        context.user_data["toning_areas_set"] = set()
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=toning_areas_kb(set()))
        return S_SVC_FLOW

    if stype == "toning_percent":
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=toning_percent_kb())
        return S_SVC_FLOW

    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    return S_SVC_FLOW


async def cb_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    flow = context.user_data.get("flow", [])
    i = context.user_data.get("flow_i", 0)
    if i >= len(flow):
        return S_TIME

    step = flow[i]
    answers = context.user_data.setdefault("services_answers", {})
    data = q.data

    if step["type"] == "toning_areas":
        sel: set[str] = context.user_data.get("toning_areas_set", set())

        if data.startswith("ta:"):
            k = data.split(":", 1)[1]
            if k in sel:
                sel.remove(k)
            else:
                sel.add(k)
            context.user_data["toning_areas_set"] = sel
            await q.edit_message_reply_markup(reply_markup=toning_areas_kb(sel))
            return S_SVC_FLOW

        if data == "ta_reset":
            sel.clear()
            context.user_data["toning_areas_set"] = sel
            await q.edit_message_reply_markup(reply_markup=toning_areas_kb(sel))
            return S_SVC_FLOW

        if data == "ta_done":
            if not sel:
                await q.message.reply_text("Выбери хотя бы одну зону 🙂")
                return S_SVC_FLOW

            label_map = {
                "rear_hemi": "Полусфера зад",
                "front_hemi": "Полусфера перед",
                "side_rear": "Боковые зад",
                "side_front": "Боковые перед",
                "windshield": "Лобовое",
                "rear_window": "Заднее стекло",
            }
            answers["toning_areas"] = [label_map.get(x, x) for x in sel]
            context.user_data["flow_i"] = i + 1
            return await ask_next_flow_step(q.message, context)

        return S_SVC_FLOW

    if step["type"] == "toning_percent":
        if data.startswith("tp:"):
            val = data.split(":", 1)[1]
            answers["toning_percent"] = val
            context.user_data["flow_i"] = i + 1
            await q.edit_message_reply_markup(reply_markup=None)
            return await ask_next_flow_step(q.message, context)
        return S_SVC_FLOW

    if step["type"] == "choice":
        prefix = f"ch:{step['key']}:"
        if data.startswith(prefix):
            idx = int(data.split(":")[-1])
            opt = step["options"][idx]
            answers[step["key"]] = opt
            context.user_data["flow_i"] = i + 1
            await q.edit_message_reply_markup(reply_markup=None)
            return await ask_next_flow_step(q.message, context)
        return S_SVC_FLOW

    if step["type"] == "yesno":
        pref = step["kb_prefix"] + ":"
        if data.startswith(pref):
            val = data.split(":")[-1]
            answers[step["key"]] = ("Да" if val == "yes" else "Нет")
            context.user_data["flow_i"] = i + 1
            await q.edit_message_reply_markup(reply_markup=None)
            return await ask_next_flow_step(q.message, context)
        return S_SVC_FLOW

    return S_SVC_FLOW


async def on_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = clean_text(update.message.text)
    dt = parse_datetime_ru(txt)
    if not dt:
        await update.message.reply_text(
            "Не понял дату/время 😅\n"
            "Напиши в формате:\n"
            "• `сегодня 18:00`\n"
            "• `завтра 12:00`\n"
            "• `25.12 14:00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return S_TIME

    if not is_future_time(dt):
        await update.message.reply_text(
            "Нужно выбрать время **в будущем** 🙂\n"
            "Например: `сегодня 18:00` или `завтра 12:00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return S_TIME

    context.user_data["visit_dt"] = dt
    await update.message.reply_text(
        "Ок! Осталось оставить удобный контакт:\n"
        "• нажми «Отправить контакт ☎️»\n"
        "• или напиши номер текстом\n"
        "• или скажи «можно сюда в Telegram»",
        reply_markup=contact_kb(),
    )
    return S_CONTACT


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number)
        if not phone:
            await update.message.reply_text(
                "Не смог распознать номер 😅\n"
                "Напиши номер текстом в формате +7... или 8...",
            )
            return S_CONTACT
        context.user_data["phone"] = phone
        context.user_data["contact_method"] = "phone"
    else:
        txt = clean_text(update.message.text)
        if any(x in txt.lower() for x in ["телег", "telegram", "tg", "сюда"]):
            context.user_data["contact_method"] = "telegram"
            context.user_data["phone"] = ""
        else:
            phone = normalize_phone(txt)
            if not phone:
                await update.message.reply_text(
                    "Номер некорректный 🙂\n"
                    "Напиши в формате `+7XXXXXXXXXX` или `8XXXXXXXXXX`,\n"
                    "либо нажми «Отправить контакт ☎️».",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return S_CONTACT
            context.user_data["phone"] = phone
            context.user_data["contact_method"] = "phone"

    await send_lead_to_manager(update, context)

    glass_has_chips = context.user_data.get("services_answers", {}).get("glass_has_chips") == "Да"
    extra = ""
    if glass_has_chips:
        extra = (
            "\n\n⚠️ По стеклу: если есть сколы/трещины — полировка/шлифовка не делается. "
            "Нужна замена стекла. Мы можем заменить — менеджер подскажет варианты."
        )

    await update.message.reply_text(
        "✅ Принято! Я передал заявку менеджеру.\n"
        "Он свяжется с тобой в ближайшее время.\n\n"
        "Пока ждёшь — можешь посмотреть наши работы 👇"
        + extra,
        reply_markup=channel_kb(),
    )
    return S_DONE


async def send_lead_to_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    user = update.effective_user

    tg_username = f"@{user.username}" if user and user.username else "—"
    tg_id = str(user.id) if user else "—"

    selected = data.get("services_selected", [])
    answers = data.get("services_answers", {})

    svc_lines = []
    for svc in selected:
        label = SERVICE_LABEL.get(svc, svc)
        svc_lines.append(f"• {label}")

        if svc == "toning":
            areas = answers.get("toning_areas")
            percent = answers.get("toning_percent")
            oldfilm = answers.get("toning_old_film")
            if areas:
                svc_lines.append(f"   └ Зоны: {', '.join(areas)}")
            if percent:
                svc_lines.append(f"   └ %: {percent}")
            if oldfilm:
                svc_lines.append(f"   └ Старая плёнка: {oldfilm}")

        if svc == "body_polish":
            v = answers.get("body_polish_goal")
            if v:
                svc_lines.append(f"   └ Цель: {v}")

        if svc == "ceramic":
            stage = answers.get("ceramic_stage")
            need = answers.get("ceramic_need")
            if stage:
                svc_lines.append(f"   └ Впервые/обновление: {stage}")
            if need:
                svc_lines.append(f"   └ Приоритет: {need}")

        if svc == "water_spots":
            v = answers.get("water_spots_where")
            if v:
                svc_lines.append(f"   └ Где сильнее: {v}")

        if svc == "anti_rain":
            v = answers.get("anti_rain_where")
            if v:
                svc_lines.append(f"   └ Куда нанести: {v}")

        if svc == "headlights":
            v = answers.get("headlights_state")
            if v:
                svc_lines.append(f"   └ Состояние: {v}")

        if svc == "glass_polish":
            v = answers.get("glass_polish_problem")
            chips = answers.get("glass_has_chips")
            if v:
                svc_lines.append(f"   └ Проблема: {v}")
            if chips:
                svc_lines.append(f"   └ Сколы/трещины: {chips}")
                if chips == "Да":
                    svc_lines.append("   └ ⚠️ Полировка/шлифовка невозможна → нужна замена стекла (можем заменить).")

        if svc == "interior":
            v = answers.get("interior_type")
            if v:
                svc_lines.append(f"   └ Что нужно: {v}")

        if svc == "engine_wash":
            v = answers.get("engine_recent")
            if v:
                svc_lines.append(f"   └ Делали ранее: {v}")

    dt: datetime = data.get("visit_dt")
    dt_str = dt.strftime("%d.%m.%Y %H:%M") if isinstance(dt, datetime) else "—"

    contact_method = data.get("contact_method", "—")
    phone = data.get("phone", "")

    temp = lead_temperature(data)

    upsells = compute_upsells(data)
    upsells_text = format_upsells_for_manager(upsells)

    text = (
        "🔥 **НОВАЯ ЗАЯВКА (RKS studio)**\n\n"
        f"**Клиент:** {data.get('name','—')}\n"
        f"**Авто:** {data.get('car','—')}\n"
        f"**Когда удобно:** {dt_str}\n"
        f"**TG:** {tg_username}\n"
        f"**TG ID:** {tg_id}\n"
        f"**Контакт:** {'Телефон' if contact_method=='phone' else 'Telegram'}\n"
        f"**Номер:** {phone if phone else '—'}\n\n"
        f"**Услуги:**\n" + "\n".join(svc_lines) + "\n\n"
        f"**Рекомендовано (апселл):**\n{upsells_text}\n\n"
        f"**Лид:** {temp}"
    )

    await context.bot.send_message(chat_id=MANAGER_ID, text=text, parse_mode=ParseMode.MARKDOWN)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


# -------------------- APP --------------------
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start), CommandHandler("restart", cmd_restart)],
        states={
            S_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_name)],
            S_CAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_car)],

            # ✅ FIX: pattern must match "svc:toning" etc., not only "svc:"
            S_SERVICES: [CallbackQueryHandler(cb_services, pattern=r"^(svc:.*|svc_done|svc_reset)$")],

            S_SVC_FLOW: [CallbackQueryHandler(cb_flow)],
            S_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_time)],
            S_CONTACT: [
                MessageHandler(filters.CONTACT, on_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_contact),
            ],
            S_DONE: [
                CallbackQueryHandler(cb_restart, pattern=r"^restart$"),
                CommandHandler("start", cmd_start),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    return app


def main():
    # health server for Render Web Service
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()

    app = build_app()

    # anti-conflict loop for free Render deployments
    while True:
        try:
            logger.info("Bot starting polling...")
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
        except Conflict:
            logger.warning("Conflict (another getUpdates). Retry in 5 seconds...")
            time.sleep(5)
        except Exception:
            logger.exception("Unexpected error. Retry in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    main()
```0