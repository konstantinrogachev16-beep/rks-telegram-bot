import os
import re
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------------- logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rks-bot")

# ---------------- env ----------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

MANAGER_CHAT_ID = int(os.getenv("MANAGER_CHAT_ID", "327140660"))
PORT = int(os.getenv("PORT", "10000"))

# Канал с работами
WORKS_CHANNEL_URL = "https://t.me/+7nQ-MkqFk_BmZTZi"

# ---------------- states ----------------
ASK_NAME, ASK_CAR, PICK_SERVICES, SERVICE_FLOW, ASK_TIME, ASK_CONTACT = range(6)

# ---------------- services ----------------
SVC_TINT = "tint"
SVC_BODY_POLISH = "body_polish"
SVC_CERAMIC = "ceramic"
SVC_WATERSPOT = "waterspot"
SVC_ANTI_RAIN = "anti_rain"
SVC_HEADLIGHT = "headlight"
SVC_GLASS_POLISH = "glass_polish"
SVC_INTERIOR = "interior"
SVC_ENGINE = "engine"

SERVICES = [
    (SVC_TINT, "Тонировка"),
    (SVC_BODY_POLISH, "Полировка кузова"),
    (SVC_CERAMIC, "Керамика (защита)"),
    (SVC_WATERSPOT, "Удаление водного камня (стёкла)"),
    (SVC_ANTI_RAIN, "Антидождь"),
    (SVC_HEADLIGHT, "Полировка фар"),
    (SVC_GLASS_POLISH, "Шлифовка/полировка стекла"),
    (SVC_INTERIOR, "Химчистка салона"),
    (SVC_ENGINE, "Мойка мотора с консервацией"),
]

# ---------------- helpers ----------------
def normalize_phone(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)
    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) < 10:
        return None

    if digits.startswith("8") and len(only_digits) == 11:
        digits = "+7" + only_digits[1:]
    elif digits.startswith("7") and len(only_digits) == 11:
        digits = "+7" + only_digits
    elif digits.startswith("+7") and len(only_digits) == 11:
        digits = "+7" + only_digits[-10:]
    return digits


def mark_selected(title: str, is_on: bool) -> str:
    return f"✅ {title}" if is_on else f"☐ {title}"


def build_services_kb(selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, title in SERVICES:
        is_on = key in selected
        rows.append([InlineKeyboardButton(mark_selected(title, is_on), callback_data=f"svc|toggle|{key}")])

    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc|done|_"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="svc|reset|_"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def ensure_user_struct(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("selected_services", [])
    context.user_data.setdefault("service_queue", [])
    context.user_data.setdefault("service_answers", {})
    context.user_data.setdefault("recommendations_sent", set())


def svc_title(svc_key: str) -> str:
    for k, t in SERVICES:
        if k == svc_key:
            return t
    return svc_key


def add_answer(context: ContextTypes.DEFAULT_TYPE, svc_key: str, field: str, value: str):
    ensure_user_struct(context)
    context.user_data["service_answers"].setdefault(svc_key, {})
    context.user_data["service_answers"][svc_key][field] = value


def get_answer(context: ContextTypes.DEFAULT_TYPE, svc_key: str, field: str, default: str = "") -> str:
    return context.user_data.get("service_answers", {}).get(svc_key, {}).get(field, default)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def lead_temperature(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    preferred_time = (context.user_data.get("preferred_time") or "").lower()
    has_phone = bool(context.user_data.get("phone"))
    services = context.user_data.get("selected_services", [])

    score = 0
    why = []

    if any(x in preferred_time for x in ["сегодня", "срочно", "прямо", "сейчас"]):
        score += 3
        why.append("хочет приехать сегодня/срочно")
    elif "завтра" in preferred_time:
        score += 2
        why.append("хочет приехать завтра")
    elif any(x in preferred_time for x in ["на этой неделе", "в течение недели", "на неделе"]):
        score += 1
        why.append("планирует в ближайшую неделю")
    elif preferred_time:
        why.append("время указано, но без срочности")
    else:
        score -= 1
        why.append("время не указано")

    if has_phone:
        score += 2
        why.append("оставил телефон")
    else:
        why.append("без телефона (только Telegram)")

    if len(services) >= 2:
        score += 1
        why.append("выбрал несколько услуг")

    if score >= 5:
        return ("ГОРЯЧИЙ 🔥", ", ".join(why))
    if score >= 2:
        return ("ТЁПЛЫЙ 🌤️", ", ".join(why))
    return ("ХОЛОДНЫЙ ❄️", ", ".join(why))


# ---------------- Render port "костыль" ----------------
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def start_http_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
        logger.info("HTTP server listening on port %s", PORT)
        server.serve_forever()
    except Exception as e:
        logger.error("HTTP server failed: %s", e)


# ---------------- flow: start / restart / cancel ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    ensure_user_struct(context)

    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя 🙂\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


# ---------------- step: name -> car ----------------
async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_struct(context)
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    await update.message.reply_text(
        "Отлично 👍\n\n"
        "Какой у тебя автомобиль?\n"
        "Напиши: марка / модель / год\n"
        "Например: Toyota Camry 2018"
    )
    return ASK_CAR


async def ask_car(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_struct(context)
    car = (update.message.text or "").strip()
    if len(car) < 4:
        await update.message.reply_text("Напиши чуть подробнее 🙂 Например: Kia Rio 2020")
        return ASK_CAR

    context.user_data["car"] = car

    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=build_services_kb(context.user_data["selected_services"]),
    )
    return PICK_SERVICES


# ---------------- step: services multiselect ----------------
async def services_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_struct(context)
    q = update.callback_query
    await q.answer()

    data = (q.data or "").split("|", 2)
    if len(data) < 3:
        return PICK_SERVICES

    _, action, payload = data
    selected = context.user_data["selected_services"]

    if action == "toggle":
        if payload in selected:
            selected.remove(payload)
        else:
            selected.append(payload)
        await q.edit_message_reply_markup(reply_markup=build_services_kb(selected))
        return PICK_SERVICES

    if action == "reset":
        selected.clear()
        await q.edit_message_reply_markup(reply_markup=build_services_kb(selected))
        return PICK_SERVICES

    if action == "done":
        if not selected:
            await q.edit_message_text("Выбери хотя бы одну услугу 🙂")
            await q.message.reply_text(
                "Выбери услуги (можно несколько) и нажми «Готово ✅».",
                reply_markup=build_services_kb(selected),
            )
            return PICK_SERVICES

        order = [k for k, _ in SERVICES]
        queue = [k for k in order if k in selected]
        context.user_data["service_queue"] = queue

        await q.edit_message_text("Отлично! Уточню пару моментов по выбранным услугам 👇")

        await maybe_send_recommendations(update, context)
        return await ask_next_service_question(update, context)

    return PICK_SERVICES


# ---------------- recommendations (no duplicates) ----------------
async def maybe_send_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected = context.user_data.get("selected_services", [])
    sent = context.user_data.get("recommendations_sent", set())

    if SVC_CERAMIC in selected and "ceramic_prep" not in sent:
        sent.add("ceramic_prep")
        await update.effective_chat.send_message(
            "💡 Если планируешь керамику — почти всегда лучше сначала сделать подготовку/лёгкую полировку. "
            "Так эффект заметно круче и держится дольше."
        )

    context.user_data["recommendations_sent"] = sent


# ---------------- service queue helpers ----------------
def current_service(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    q = context.user_data.get("service_queue", [])
    return q[0] if q else None


def pop_service(context: ContextTypes.DEFAULT_TYPE):
    q = context.user_data.get("service_queue", [])
    if q:
        q.pop(0)
    context.user_data["service_queue"] = q


async def ask_next_service_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_struct(context)
    svc = current_service(context)
    if not svc:
        await update.effective_chat.send_message(
            "Когда тебе удобно подъехать? Напиши день/время (например: «сегодня после 18:00» или «в пятницу 12:00»)."
        )
        return ASK_TIME

    if svc == SVC_TINT:
        return await tint_step_1(update, context)
    if svc == SVC_BODY_POLISH:
        return await body_polish_step_1(update, context)
    if svc == SVC_CERAMIC:
        return await ceramic_step_1(update, context)
    if svc == SVC_WATERSPOT:
        return await waterspot_step_1(update, context)
    if svc == SVC_ANTI_RAIN:
        return await anti_rain_step_1(update, context)
    if svc == SVC_HEADLIGHT:
        return await headlight_step_1(update, context)
    if svc == SVC_GLASS_POLISH:
        return await glass_polish_step_1(update, context)
    if svc == SVC_INTERIOR:
        return await interior_step_1(update, context)
    if svc == SVC_ENGINE:
        return await engine_step_1(update, context)

    pop_service(context)
    return await ask_next_service_question(update, context)


def kb_single(prefix: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text, callback_data=f"{prefix}|{val}")] for text, val in options]
    return InlineKeyboardMarkup(rows)


def kb_multi(prefix: str, options: list[tuple[str, str]], selected: set[str], done_cb: str, reset_cb: str) -> InlineKeyboardMarkup:
    rows = []
    for text, val in options:
        on = val in selected
        rows.append([InlineKeyboardButton(("✅ " if on else "☐ ") + text, callback_data=f"{prefix}|toggle|{val}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data=done_cb),
            InlineKeyboardButton("Сбросить ↩️", callback_data=reset_cb),
        ]
    )
    return InlineKeyboardMarkup(rows)


# -------- TINT (обновлено: вопрос про старую плёнку) --------
async def tint_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = SVC_TINT
    context.user_data["flow_svc"] = svc
    context.user_data["flow_step"] = "tint_area"

    selected = set(get_answer(context, svc, "areas", "").split(",")) if get_answer(context, svc, "areas") else set()

    options = [
        ("Полусфера зад", "rear_half"),
        ("Полусфера перед", "front_half"),
        ("Боковые зад", "rear_sides"),
        ("Боковые перед", "front_sides"),
        ("Лобовое", "windshield"),
        ("Заднее", "rear_glass"),
    ]

    await update.effective_chat.send_message(
        f"{svc_title(svc)}\nКакие стёкла тонируем? (можно несколько) 👇",
        reply_markup=kb_multi(
            prefix="tint|area",
            options=options,
            selected=selected,
            done_cb="tint|area|done",
            reset_cb="tint|area|reset",
        ),
    )
    return SERVICE_FLOW


async def tint_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = SVC_TINT
    context.user_data["flow_svc"] = svc
    context.user_data["flow_step"] = "tint_percent"

    await update.effective_chat.send_message(
        "Какой процент плёнки хочешь? (чем меньше %, тем темнее)\n"
        "Если не знаешь — выбери «Не знаю» 🙂",
        reply_markup=kb_single(
            "tint|percent",
            [
                ("2% (очень темно)", "2"),
                ("5%", "5"),
                ("15%", "15"),
                ("20%", "20"),
                ("35%", "35"),
                ("50%", "50"),
                ("Не знаю", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


async def tint_step_3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # НОВОЕ: старая плёнка
    svc = SVC_TINT
    context.user_data["flow_svc"] = svc
    context.user_data["flow_step"] = "tint_old_film"

    await update.effective_chat.send_message(
        "Есть старая плёнка, которую нужно снять перед тонировкой?",
        reply_markup=kb_single(
            "tint|old",
            [
                ("Да, есть старая плёнка", "yes"),
                ("Нет, плёнки нет", "no"),
                ("Не знаю / возможно", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- BODY POLISH --------
async def body_polish_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = SVC_BODY_POLISH
    await update.effective_chat.send_message(
        f"{svc_title(svc)}\nКакая цель полировки?",
        reply_markup=kb_single(
            "polish|goal",
            [
                ("Освежить блеск", "shine"),
                ("Убрать мелкие царапины/паутинку", "micro_scratches"),
                ("Под продажу", "sale"),
                ("После покраски/ремонта", "after_repair"),
            ],
        ),
    )
    return SERVICE_FLOW


async def body_polish_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "Есть глубокие царапины/сколы, которые цепляются ногтем?",
        reply_markup=kb_single(
            "polish|damage",
            [
                ("Да, есть", "yes"),
                ("Нет, в основном мелкие", "no"),
                ("Не знаю/нужно посмотреть", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- CERAMIC --------
async def ceramic_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_CERAMIC)}\nДля чего керамика в первую очередь?",
        reply_markup=kb_single(
            "ceramic|goal",
            [
                ("Защита + блеск", "protect_shine"),
                ("Гидрофоб/чтобы вода скатывалась", "hydro"),
                ("Чтобы машина легче мылась", "easy_wash"),
                ("Под продажу / внешний вид", "sale"),
            ],
        ),
    )
    return SERVICE_FLOW


async def ceramic_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "Состояние ЛКП сейчас?",
        reply_markup=kb_single(
            "ceramic|paint",
            [
                ("Почти новое", "new"),
                ("Есть паутинка/мелкие царапины", "micro"),
                ("Есть заметные царапины/матовость", "visible"),
                ("Не знаю, нужно посмотреть", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- WATERSPOT --------
async def waterspot_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_WATERSPOT)}\nГде налёт/водный камень?",
        reply_markup=kb_single(
            "ws|where",
            [
                ("Лобовое", "windshield"),
                ("Боковые", "sides"),
                ("Заднее", "rear"),
                ("Везде", "all"),
            ],
        ),
    )
    return SERVICE_FLOW


async def waterspot_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "Насколько сильный налёт?",
        reply_markup=kb_single(
            "ws|level",
            [
                ("Лёгкий (пленка/разводы)", "light"),
                ("Средний (заметные пятна)", "medium"),
                ("Сильный (очень заметно)", "hard"),
                ("Не знаю", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- ANTI RAIN --------
async def anti_rain_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_ANTI_RAIN)}\nКуда наносим?",
        reply_markup=kb_single(
            "ar|where",
            [
                ("Лобовое", "windshield"),
                ("Все стёкла", "all"),
                ("Лобовое + зеркала", "windshield_mirrors"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- HEADLIGHT --------
async def headlight_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_HEADLIGHT)}\nСостояние фар?",
        reply_markup=kb_single(
            "hl|state",
            [
                ("Мутные/пожелтели", "yellow"),
                ("Царапины/затёртость", "scratches"),
                ("Просто освежить", "refresh"),
                ("Не знаю", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- GLASS POLISH --------
async def glass_polish_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_GLASS_POLISH)}\nКакое стекло шлифуем/полируем?",
        reply_markup=kb_single(
            "gp|where",
            [
                ("Лобовое", "windshield"),
                ("Боковое", "side"),
                ("Заднее", "rear"),
                ("Несколько/все", "many"),
            ],
        ),
    )
    return SERVICE_FLOW


async def glass_polish_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "Какие дефекты?",
        reply_markup=kb_single(
            "gp|level",
            [
                ("Мелкие царапины/дворники", "light"),
                ("Средние царапины", "medium"),
                ("Сильные/сколы/глубокие", "hard"),
                ("Не знаю", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- INTERIOR --------
async def interior_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_INTERIOR)}\nВыбери формат:",
        reply_markup=kb_single(
            "int|type",
            [
                ("Экспресс уборка", "express"),
                ("Полная химчистка", "full"),
                ("Чистка кожи + пропитка", "leather"),
            ],
        ),
    )
    return SERVICE_FLOW


async def interior_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = get_answer(context, SVC_INTERIOR, "type")

    if t == "express":
        await update.effective_chat.send_message(
            "Экспресс уборка: на что упор?",
            reply_markup=kb_single(
                "int|express",
                [
                    ("Пыль/салон + коврики", "dust_mats"),
                    ("Пылесос + пластик", "vac_plastic"),
                    ("Быстро освежить перед поездкой", "fresh"),
                ],
            ),
        )
        return SERVICE_FLOW

    if t == "leather":
        await update.effective_chat.send_message(
            "Кожа: что чистим и пропитываем?",
            reply_markup=kb_single(
                "int|leather",
                [
                    ("Только сиденья", "seats"),
                    ("Сиденья + руль", "seats_wheel"),
                    ("Весь кожаный салон", "all"),
                ],
            ),
        )
        return SERVICE_FLOW

    await update.effective_chat.send_message(
        "Полная химчистка: что беспокоит больше всего?",
        reply_markup=kb_single(
            "int|full",
            [
                ("Пятна/грязь", "stains"),
                ("Запах", "smell"),
                ("Дети/животные", "kids_pets"),
                ("Просто сделать как новый", "like_new"),
            ],
        ),
    )
    return SERVICE_FLOW


# -------- ENGINE --------
async def engine_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"{svc_title(SVC_ENGINE)}\nЗачем моем мотор?",
        reply_markup=kb_single(
            "eng|reason",
            [
                ("Под продажу", "sale"),
                ("Убрать грязь/масляные следы", "dirty"),
                ("Профилактика/аккуратно привести в порядок", "care"),
                ("Не знаю, просто хочу чисто", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


async def engine_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "Нужна консервация (защитный состав) после мойки?",
        reply_markup=kb_single(
            "eng|cons",
            [
                ("Да, нужно", "yes"),
                ("Нет, только мойка", "no"),
                ("Не знаю, посоветуйте", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


# ---------------- callback handler for service flow ----------------
async def steps_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_struct(context)
    q = update.callback_query
    await q.answer()

    data = (q.data or "")
    parts = data.split("|")
    if not parts:
        return SERVICE_FLOW

    # --- tint multi area ---
    if parts[0] == "tint" and len(parts) >= 3 and parts[1] == "area":
        svc = SVC_TINT
        current = set(get_answer(context, svc, "areas", "").split(",")) if get_answer(context, svc, "areas") else set()

        if parts[2] == "toggle" and len(parts) == 4:
            val = parts[3]
            if val in current:
                current.remove(val)
            else:
                current.add(val)

            add_answer(context, svc, "areas", ",".join(sorted(current)))

            options = [
                ("Полусфера зад", "rear_half"),
                ("Полусфера перед", "front_half"),
                ("Боковые зад", "rear_sides"),
                ("Боковые перед", "front_sides"),
                ("Лобовое", "windshield"),
                ("Заднее", "rear_glass"),
            ]
            await q.edit_message_reply_markup(
                reply_markup=kb_multi(
                    prefix="tint|area",
                    options=options,
                    selected=current,
                    done_cb="tint|area|done",
                    reset_cb="tint|area|reset",
                )
            )
            return SERVICE_FLOW

        if parts[2] == "reset":
            add_answer(context, svc, "areas", "")
            options = [
                ("Полусфера зад", "rear_half"),
                ("Полусфера перед", "front_half"),
                ("Боковые зад", "rear_sides"),
                ("Боковые перед", "front_sides"),
                ("Лобовое", "windshield"),
                ("Заднее", "rear_glass"),
            ]
            await q.edit_message_reply_markup(
                reply_markup=kb_multi(
                    prefix="tint|area",
                    options=options,
                    selected=set(),
                    done_cb="tint|area|done",
                    reset_cb="tint|area|reset",
                )
            )
            return SERVICE_FLOW

        if parts[2] == "done":
            if not current:
                await q.message.reply_text("Выбери хотя бы один пункт 🙂")
                return SERVICE_FLOW
            await q.message.reply_text("Принял ✅")
            return await tint_step_2(update, context)

    # --- helper for single ---
    def handle_single(prefix: str) -> str | None:
        if data.startswith(prefix + "|") and len(parts) == 3:
            return parts[2]
        return None

    # tint percent -> next old film
    val = handle_single("tint|percent")
    if val is not None:
        add_answer(context, SVC_TINT, "percent", val)
        await q.message.reply_text("Ок ✅")
        return await tint_step_3(update, context)

    # tint old film -> finish tint
    val = handle_single("tint|old")
    if val is not None:
        add_answer(context, SVC_TINT, "old_film", val)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # polish
    val = handle_single("polish|goal")
    if val is not None:
        add_answer(context, SVC_BODY_POLISH, "goal", val)
        if SVC_CERAMIC in context.user_data.get("selected_services", []):
            await q.message.reply_text("💡 Совет: перед керамикой полировка/подготовка почти всегда даёт лучший эффект.")
        return await body_polish_step_2(update, context)

    val = handle_single("polish|damage")
    if val is not None:
        add_answer(context, SVC_BODY_POLISH, "damage", val)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # ceramic
    val = handle_single("ceramic|goal")
    if val is not None:
        add_answer(context, SVC_CERAMIC, "goal", val)
        return await ceramic_step_2(update, context)

    val = handle_single("ceramic|paint")
    if val is not None:
        add_answer(context, SVC_CERAMIC, "paint", val)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # waterspot
    val = handle_single("ws|where")
    if val is not None:
        add_answer(context, SVC_WATERSPOT, "where", val)
        return await waterspot_step_2(update, context)

    val = handle_single("ws|level")
    if val is not None:
        add_answer(context, SVC_WATERSPOT, "level", val)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # anti-rain
    val = handle_single("ar|where")
    if val is not None:
        add_answer(context, SVC_ANTI_RAIN, "where", val)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # headlight
    val = handle_single("hl|state")
    if val is not None:
        add_answer(context, SVC_HEADLIGHT, "state", val)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # glass polish
    val = handle_single("gp|where")
    if val is not None:
        add_answer(context, SVC_GLASS_POLISH, "where", val)
        return await glass_polish_step_2(update, context)

    val = handle_single("gp|level")
    if val is not None:
        add_answer(context, SVC_GLASS_POLISH, "level", val)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # interior
    val = handle_single("int|type")
    if val is not None:
        add_answer(context, SVC_INTERIOR, "type", val)
        return await interior_step_2(update, context)

    val = handle_single("int|express")
    if val is not None:
        add_answer(context, SVC_INTERIOR, "express_focus", val)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    val = handle_single("int|leather")
    if val is not None:
        add_answer(context, SVC_INTERIOR, "leather_where", val)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    val = handle_single("int|full")
    if val is not None:
        add_answer(context, SVC_INTERIOR, "full_issue", val)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # engine
    val = handle_single("eng|reason")
    if val is not None:
        add_answer(context, SVC_ENGINE, "reason", val)
        return await engine_step_2(update, context)

    val = handle_single("eng|cons")
    if val is not None:
        add_answer(context, SVC_ENGINE, "conserve", val)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    return SERVICE_FLOW


# ---------------- time -> contact ----------------
async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 2:
        await update.message.reply_text("Напиши удобное время чуть точнее 🙂")
        return ASK_TIME

    context.user_data["preferred_time"] = txt

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


# ---------------- contact -> send lead ----------------
async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = ""
    contact_method = "telegram"

    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        contact_method = "phone"
    else:
        txt = (update.message.text or "").strip()
        if "телег" in txt.lower() or "сюда" in txt.lower() or "tg" in txt.lower():
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
            phone = p
            contact_method = "phone"

    context.user_data["phone"] = phone
    context.user_data["contact_method"] = contact_method

    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "(нет username)"
    name = context.user_data.get("name", "")
    car = context.user_data.get("car", "—")
    preferred_time = context.user_data.get("preferred_time", "")
    selected = context.user_data.get("selected_services", [])
    answers = context.user_data.get("service_answers", {})

    temp, why = lead_temperature(context)

    lines = []
    for svc in selected:
        svc_lines = [f"• {svc_title(svc)}"]
        a = answers.get(svc, {})
        if a:
            for k, v in a.items():
                if not v:
                    continue
                svc_lines.append(f"   - {k}: {v}")
        lines.append("\n".join(svc_lines))

    lead_text = (
        "🔥 *НОВЫЙ ЛИД*\n"
        f"*Температура:* {temp}\n"
        f"*Почему:* {why}\n"
        f"*Время:* {now_str()}\n\n"
        f"*Имя:* {name}\n"
        f"*Автомобиль:* {car}\n"
        f"*TG:* {username}\n\n"
        f"*Услуги:*\n" + ("\n".join(lines) if lines else "—") + "\n\n"
        f"*Когда удобно:* {preferred_time}\n"
        f"*Контакт:* {(phone if phone else 'Telegram')}\n"
    )

    try:
        await context.bot.send_message(
            chat_id=MANAGER_CHAT_ID,
            text=lead_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("Failed to send lead to manager: %s", e)

    # клиенту: подтверждение + канал работ
    restart_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("Пройти заново 🔄")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    channel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Смотреть наши работы 🔥", url=WORKS_CHANNEL_URL)]]
    )

    await update.message.reply_text(
        "✅ Принято! Я передал информацию менеджеру.\n"
        "Он свяжется с тобой в ближайшее время.\n\n"
        "Пока ждёшь — можешь посмотреть наши работы 👇",
        reply_markup=restart_kb,
    )
    await update.message.reply_text(
        "Перейти в Telegram-канал с работами:",
        reply_markup=channel_kb,
        disable_web_page_preview=True,
    )

    return ConversationHandler.END


# ---------------- restart button handler ----------------
async def restart_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip().lower()
    if "заново" in txt:
        return await start(update, context)
    return ConversationHandler.END


# ---------------- main ----------------
def main():
    t = threading.Thread(target=start_http_server, daemon=True)
    t.start()

    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("restart", restart),
        ],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_CAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_car)],
            PICK_SERVICES: [CallbackQueryHandler(services_cb, pattern=r"^svc\|")],
            SERVICE_FLOW: [CallbackQueryHandler(steps_cb)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(r"(?i)^пройти заново"), restart_button),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    async def _post_init(application: Application):
        try:
            await application.bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            logger.warning("delete_webhook failed: %s", e)

    app.post_init = _post_init

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()