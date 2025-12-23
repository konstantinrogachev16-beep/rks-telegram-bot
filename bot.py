import os
import re
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

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

# ---------------- translations (для менеджера) ----------------
TINT_AREAS_RU = {
    "rear_half": "Полусфера зад",
    "front_half": "Полусфера перед",
    "rear_sides": "Боковые зад",
    "front_sides": "Боковые перед",
    "windshield": "Лобовое",
    "rear_glass": "Заднее",
}
YESNO_RU = {"yes": "Да", "no": "Нет", "unknown": "Не знаю/возможно"}

POLISH_GOAL_RU = {
    "shine": "Освежить блеск",
    "micro_scratches": "Убрать мелкие царапины/паутинку",
    "sale": "Под продажу",
    "after_repair": "После покраски/ремонта",
}
POLISH_DAMAGE_RU = {"yes": "Да, есть", "no": "Нет, в основном мелкие", "unknown": "Не знаю/нужно посмотреть"}

CERAMIC_GOAL_RU = {
    "protect_shine": "Защита + блеск",
    "hydro": "Гидрофоб (вода скатывается)",
    "easy_wash": "Чтобы легче мыть",
    "sale": "Под продажу / внешний вид",
}
CERAMIC_PAINT_RU = {
    "new": "Почти новое",
    "micro": "Есть паутинка/мелкие царапины",
    "visible": "Есть заметные царапины/матовость",
    "unknown": "Не знаю, нужно посмотреть",
}

WS_WHERE_RU = {"windshield": "Лобовое", "sides": "Боковые", "rear": "Заднее", "all": "Везде"}
WS_LEVEL_RU = {"light": "Лёгкий", "medium": "Средний", "hard": "Сильный", "unknown": "Не знаю"}

AR_WHERE_RU = {"windshield": "Лобовое", "all": "Все стёкла", "windshield_mirrors": "Лобовое + зеркала"}

HL_STATE_RU = {"yellow": "Мутные/пожелтели", "scratches": "Царапины/затёртость", "refresh": "Просто освежить", "unknown": "Не знаю"}

GP_WHERE_RU = {"windshield": "Лобовое", "side": "Боковое", "rear": "Заднее", "many": "Несколько/все"}
GP_LEVEL_RU = {"light": "Мелкие царапины/дворники", "medium": "Средние царапины", "hard": "Сильные/глубокие/сколы", "unknown": "Не знаю"}

INT_TYPE_RU = {"express": "Экспресс уборка", "full": "Полная химчистка", "leather": "Чистка кожи + пропитка"}
INT_EXPRESS_RU = {"dust_mats": "Пыль/салон + коврики", "vac_plastic": "Пылесос + пластик", "fresh": "Быстро освежить"}
INT_LEATHER_RU = {"seats": "Только сиденья", "seats_wheel": "Сиденья + руль", "all": "Весь кожаный салон"}
INT_FULL_RU = {"stains": "Пятна/грязь", "smell": "Запах", "kids_pets": "Дети/животные", "like_new": "Сделать как новый"}

ENG_REASON_RU = {"sale": "Под продажу", "dirty": "Убрать грязь/масляные следы", "care": "Профилактика/аккуратно", "unknown": "Не знаю"}
ENG_CONS_RU = {"yes": "Да, с консервацией", "no": "Нет, только мойка", "unknown": "Не знаю — посоветовать"}

# ---------------- helpers ----------------
def normalize_phone_strict(s: str) -> str | None:
    """
    Строгая проверка телефона:
    - 10..15 цифр
    - если РФ и 11 цифр: 8XXXXXXXXXX / 7XXXXXXXXXX -> +7XXXXXXXXXX
    - иначе если начинается с + и длина ок -> оставляем
    """
    if not s:
        return None
    s = s.strip()
    raw = re.sub(r"[^\d+]", "", s)
    digits_only = re.sub(r"\D", "", raw)

    if not (10 <= len(digits_only) <= 15):
        return None

    # РФ приведение
    if len(digits_only) == 11:
        if digits_only.startswith("8"):
            return "+7" + digits_only[1:]
        if digits_only.startswith("7"):
            return "+7" + digits_only[1:]
        if raw.startswith("+7"):
            return "+7" + digits_only[-10:]

    # Международный
    if raw.startswith("+"):
        return "+" + digits_only

    # Если без +, но длина ок — вернём как цифры (менеджеру можно перезвонить)
    # но для РФ 10 цифр без кода страны — оставим как есть
    return digits_only


DATE_RE = re.compile(
    r"^\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?(?:\s+(\d{1,2}):(\d{2}))?\s*$"
)

def parse_when_to_dt(text: str) -> tuple[datetime | None, str | None]:
    """
    Принимает:
    - сегодня / завтра / послезавтра (+ возможно время)
    - ДД.ММ
    - ДД.ММ.ГГГГ
    - + опционально ЧЧ:ММ
    Возвращает (dt, error)
    """
    if not text:
        return None, "Пусто"

    t = text.strip().lower()
    now = datetime.now()

    # запрет "вчера"
    if "вчера" in t:
        return None, "Нельзя выбрать прошедшее время 🙂"

    # слова + время
    def extract_time_from_text(s: str) -> tuple[int, int] | None:
        m = re.search(r"(\d{1,2}):(\d{2})", s)
        if not m:
            return None
        hh = int(m.group(1))
        mm = int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
        return None

    hhmm = extract_time_from_text(t)

    if "сегодня" in t:
        dt = now.replace(second=0, microsecond=0)
        if hhmm:
            dt = dt.replace(hour=hhmm[0], minute=hhmm[1])
        else:
            dt = dt.replace(hour=12, minute=0)
        return dt, None

    if "завтра" in t:
        base = now + timedelta(days=1)
        dt = base.replace(second=0, microsecond=0)
        if hhmm:
            dt = dt.replace(hour=hhmm[0], minute=hhmm[1])
        else:
            dt = dt.replace(hour=12, minute=0)
        return dt, None

    if "послезавтра" in t:
        base = now + timedelta(days=2)
        dt = base.replace(second=0, microsecond=0)
        if hhmm:
            dt = dt.replace(hour=hhmm[0], minute=hhmm[1])
        else:
            dt = dt.replace(hour=12, minute=0)
        return dt, None

    # дата форматом
    m = DATE_RE.match(text)
    if not m:
        # если не можем распарсить — разрешим как текст, но без проверки "в прошлом"
        # здесь лучше мягко попросить формат, чтобы работал запрет прошлого
        return None, "Напиши в формате: «сегодня 18:00» или «25.12 12:00» 🙂"

    d = int(m.group(1))
    mo = int(m.group(2))
    y = m.group(3)
    hh = m.group(4)
    mm = m.group(5)

    year = now.year
    if y:
        yy = int(y)
        if yy < 100:
            year = 2000 + yy
        else:
            year = yy

    hour = int(hh) if hh is not None else 12
    minute = int(mm) if mm is not None else 0

    try:
        dt = datetime(year, mo, d, hour, minute, 0, 0)
    except ValueError:
        return None, "Дата выглядит некорректно 🙂 Пример: 25.12 18:00"

    # если ввели без года, но дата уже прошла — считаем, что имели в виду следующий год
    if not y and dt.date() < now.date():
        try:
            dt2 = datetime(year + 1, mo, d, hour, minute, 0, 0)
            dt = dt2
        except ValueError:
            pass

    return dt, None


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


async def ask_next_service_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_struct(context)
    svc = current_service(context)
    if not svc:
        await update.effective_chat.send_message(
            "Когда тебе удобно подъехать?\n"
            "Напиши: «сегодня 18:00» или «25.12 12:00» 🙂"
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


# -------- TINT --------
async def tint_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = SVC_TINT
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
                ("50%", "50%"),
                ("Не знаю", "unknown"),
            ],
        ),
    )
    return SERVICE_FLOW


async def tint_step_3(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.effective_chat.send_message(
        f"{svc_title(SVC_BODY_POLISH)}\nКакая цель полировки?",
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

    # tint percent -> old film
    v = handle_single("tint|percent")
    if v is not None:
        add_answer(context, SVC_TINT, "percent", v)
        await q.message.reply_text("Ок ✅")
        return await tint_step_3(update, context)

    # tint old film -> finish tint
    v = handle_single("tint|old")
    if v is not None:
        add_answer(context, SVC_TINT, "old_film", v)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # polish
    v = handle_single("polish|goal")
    if v is not None:
        add_answer(context, SVC_BODY_POLISH, "goal", v)
        if SVC_CERAMIC in context.user_data.get("selected_services", []):
            await q.message.reply_text("💡 Совет: перед керамикой полировка/подготовка почти всегда даёт лучший эффект.")
        return await body_polish_step_2(update, context)

    v = handle_single("polish|damage")
    if v is not None:
        add_answer(context, SVC_BODY_POLISH, "damage", v)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # ceramic
    v = handle_single("ceramic|goal")
    if v is not None:
        add_answer(context, SVC_CERAMIC, "goal", v)
        return await ceramic_step_2(update, context)

    v = handle_single("ceramic|paint")
    if v is not None:
        add_answer(context, SVC_CERAMIC, "paint", v)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # waterspot
    v = handle_single("ws|where")
    if v is not None:
        add_answer(context, SVC_WATERSPOT, "where", v)
        return await waterspot_step_2(update, context)

    v = handle_single("ws|level")
    if v is not None:
        add_answer(context, SVC_WATERSPOT, "level", v)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # anti-rain
    v = handle_single("ar|where")
    if v is not None:
        add_answer(context, SVC_ANTI_RAIN, "where", v)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # headlight
    v = handle_single("hl|state")
    if v is not None:
        add_answer(context, SVC_HEADLIGHT, "state", v)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # glass polish
    v = handle_single("gp|where")
    if v is not None:
        add_answer(context, SVC_GLASS_POLISH, "where", v)
        return await glass_polish_step_2(update, context)

    v = handle_single("gp|level")
    if v is not None:
        add_answer(context, SVC_GLASS_POLISH, "level", v)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # interior
    v = handle_single("int|type")
    if v is not None:
        add_answer(context, SVC_INTERIOR, "type", v)
        return await interior_step_2(update, context)

    v = handle_single("int|express")
    if v is not None:
        add_answer(context, SVC_INTERIOR, "express_focus", v)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    v = handle_single("int|leather")
    if v is not None:
        add_answer(context, SVC_INTERIOR, "leather_where", v)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    v = handle_single("int|full")
    if v is not None:
        add_answer(context, SVC_INTERIOR, "full_issue", v)
        await q.message.reply_text("Принял ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    # engine
    v = handle_single("eng|reason")
    if v is not None:
        add_answer(context, SVC_ENGINE, "reason", v)
        return await engine_step_2(update, context)

    v = handle_single("eng|cons")
    if v is not None:
        add_answer(context, SVC_ENGINE, "conserve", v)
        await q.message.reply_text("Ок ✅")
        pop_service(context)
        return await ask_next_service_question(update, context)

    return SERVICE_FLOW


# ---------------- time (запрет прошлого) ----------------
async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    dt, err = parse_when_to_dt(txt)
    if err:
        await update.message.reply_text(err)
        return ASK_TIME

    now = datetime.now()
    if dt is not None and dt < now:
        await update.message.reply_text("Это время уже прошло 🙂 Напиши другое: «сегодня 18:00» или «25.12 12:00».")
        return ASK_TIME

    # сохраняем исходный текст + нормализованную дату (для менеджера можно вывести красивее)
    context.user_data["preferred_time"] = txt
    if dt is not None:
        context.user_data["preferred_time_dt"] = dt.isoformat()
    else:
        context.user_data["preferred_time_dt"] = ""

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


# ---------------- contact (строгая проверка) ----------------
async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = ""
    contact_method = "telegram"

    if update.message.contact and update.message.contact.phone_number:
        phone_norm = normalize_phone_strict(update.message.contact.phone_number)
        if not phone_norm:
            await update.message.reply_text("Контакт пришёл некорректным 😕 Попробуй написать номер текстом: +7XXXXXXXXXX")
            return ASK_CONTACT
        phone = phone_norm
        contact_method = "phone"
    else:
        txt = (update.message.text or "").strip()
        low = txt.lower()

        if "телег" in low or "сюда" in low or "tg" in low:
            contact_method = "telegram"
            phone = ""
        else:
            phone_norm = normalize_phone_strict(txt)
            if not phone_norm:
                await update.message.reply_text(
                    "Номер некорректный 🙂\n"
                    "Напиши в формате +7XXXXXXXXXX или 8XXXXXXXXXX (10–15 цифр)."
                )
                return ASK_CONTACT
            phone = phone_norm
            contact_method = "phone"

    context.user_data["phone"] = phone
    context.user_data["contact_method"] = contact_method

    # --- сбор лида ---
    user = update.effective_user
    username = user.username if user and user.username else ""
    tg_line = f"*Telegram:* @{username}\n" if username else ""

    name = context.user_data.get("name", "")
    car = context.user_data.get("car", "—")
    preferred_time = context.user_data.get("preferred_time", "")

    selected = context.user_data.get("selected_services", [])
    answers = context.user_data.get("service_answers", {})

    temp, why = lead_temperature(context)

    # --- формируем услуги на русском ---
    lines = []
    for svc in selected:
        svc_lines = [f"• *{svc_title(svc)}*"]
        a = answers.get(svc, {})

        if svc == SVC_TINT:
            areas = a.get("areas", "")
            percent = a.get("percent", "")
            old = a.get("old_film", "")

            if areas:
                area_list = [TINT_AREAS_RU.get(x, x) for x in areas.split(",") if x]
                svc_lines.append(f"   - Стёкла: {', '.join(area_list)}")
            if percent:
                svc_lines.append(f"   - Процент: {('Не знаю' if percent == 'unknown' else str(percent).replace('%','') + '%')}")
            if old:
                svc_lines.append(f"   - Старая плёнка: {YESNO_RU.get(old, old)}")

        elif svc == SVC_BODY_POLISH:
            goal = a.get("goal", "")
            dmg = a.get("damage", "")
            if goal:
                svc_lines.append(f"   - Цель: {POLISH_GOAL_RU.get(goal, goal)}")
            if dmg:
                svc_lines.append(f"   - Глубокие дефекты: {POLISH_DAMAGE_RU.get(dmg, dmg)}")

        elif svc == SVC_CERAMIC:
            goal = a.get("goal", "")
            paint = a.get("paint", "")
            if goal:
                svc_lines.append(f"   - Цель: {CERAMIC_GOAL_RU.get(goal, goal)}")
            if paint:
                svc_lines.append(f"   - ЛКП: {CERAMIC_PAINT_RU.get(paint, paint)}")

        elif svc == SVC_WATERSPOT:
            wh = a.get("where", "")
            lvl = a.get("level", "")
            if wh:
                svc_lines.append(f"   - Где: {WS_WHERE_RU.get(wh, wh)}")
            if lvl:
                svc_lines.append(f"   - Налёт: {WS_LEVEL_RU.get(lvl, lvl)}")

        elif svc == SVC_ANTI_RAIN:
            wh = a.get("where", "")
            if wh:
                svc_lines.append(f"   - Куда: {AR_WHERE_RU.get(wh, wh)}")

        elif svc == SVC_HEADLIGHT:
            st = a.get("state", "")
            if st:
                svc_lines.append(f"   - Состояние: {HL_STATE_RU.get(st, st)}")

        elif svc == SVC_GLASS_POLISH:
            wh = a.get("where", "")
            lvl = a.get("level", "")
            if wh:
                svc_lines.append(f"   - Стекло: {GP_WHERE_RU.get(wh, wh)}")
            if lvl:
                svc_lines.append(f"   - Дефекты: {GP_LEVEL_RU.get(lvl, lvl)}")

        elif svc == SVC_INTERIOR:
            t = a.get("type", "")
            if t:
                svc_lines.append(f"   - Формат: {INT_TYPE_RU.get(t, t)}")
                if t == "express":
                    ef = a.get("express_focus", "")
                    if ef:
                        svc_lines.append(f"   - Упор: {INT_EXPRESS_RU.get(ef, ef)}")
                elif t == "leather":
                    lw = a.get("leather_where", "")
                    if lw:
                        svc_lines.append(f"   - Кожа: {INT_LEATHER_RU.get(lw, lw)}")
                elif t == "full":
                    fi = a.get("full_issue", "")
                    if fi:
                        svc_lines.append(f"   - Проблема: {INT_FULL_RU.get(fi, fi)}")

        elif svc == SVC_ENGINE:
            r = a.get("reason", "")
            c = a.get("conserve", "")
            if r:
                svc_lines.append(f"   - Зачем: {ENG_REASON_RU.get(r, r)}")
            if c:
                svc_lines.append(f"   - Консервация: {ENG_CONS_RU.get(c, c)}")

        lines.append("\n".join(svc_lines))

    lead_text = (
        "🔥 *НОВЫЙ ЛИД*\n"
        f"*Температура:* {temp}\n"
        f"*Почему:* {why}\n"
        f"*Время заявки:* {now_str()}\n\n"
        f"*Имя:* {name}\n"
        f"*Автомобиль:* {car}\n"
        + tg_line +
        "\n*Услуги:*\n" + ("\n".join(lines) if lines else "—") + "\n\n"
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