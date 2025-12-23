import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

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
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =======================
# ENV
# =======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "327140660"))

# =======================
# STATES
# =======================
ASK_NAME, PICK_SERVICES, SERVICE_FLOW, ASK_TIME, ASK_CONTACT = range(5)

# =======================
# SERVICES (мультивыбор)
# =======================
SERVICES: Dict[str, str] = {
    "tint": "Тонировка",
    "body_polish": "Полировка кузова",
    "ceramic": "Керамика (защита)",
    "water_spots": "Удаление водного камня (стёкла)",
    "anti_rain": "Антидождь",
    "headlights": "Полировка фар",
    "glass_polish": "Шлифовка/полировка стекла",
    "interior_clean": "Химчистка салона",
    "engine_wash": "Мойка мотора + консервация",
}

def order_services(keys: List[str]) -> List[str]:
    order = [
        "tint",
        "body_polish",
        "ceramic",
        "water_spots",
        "anti_rain",
        "headlights",
        "glass_polish",
        "interior_clean",
        "engine_wash",
    ]
    return [k for k in order if k in keys] + [k for k in keys if k not in order]

# =======================
# STEPS
# =======================
@dataclass
class Step:
    kind: str  # "single" | "multi" | "text"
    title: str
    options: Optional[List[Tuple[str, str]]] = None
    hint: str = ""
    store_key: str = ""

SERVICE_STEPS: Dict[str, List[Step]] = {
    "tint": [
        Step(
            kind="multi",
            title="Тонировка: что тонируем? (можно несколько)",
            options=[
                ("rear_half", "Полусфера зад"),
                ("front_sides", "Боковые перед"),
                ("rear_sides", "Боковые зад"),
                ("windshield", "Лобовое"),
                ("rear_window", "Заднее"),
            ],
            hint="💡 Если часто ездишь ночью — лучше 15–35%. 5% очень темно.",
            store_key="tint_parts",
        ),
        Step(
            kind="single",
            title="Тонировка: какая темнота нужна?",
            options=[
                ("2", "2%"),
                ("5", "5%"),
                ("15", "15%"),
                ("35", "35%"),
                ("50", "50%"),
                ("idk", "Не знаю"),
            ],
            hint="💡 15–35% обычно самый комфортный вариант.",
            store_key="tint_darkness",
        ),
        Step(
            kind="single",
            title="Тонировка: цель?",
            options=[
                ("privacy", "Приватность"),
                ("sun", "Защита от солнца/жары"),
                ("night", "Комфорт ночью"),
                ("look", "Внешний вид"),
                ("mix", "Смешанное"),
            ],
            hint="",
            store_key="tint_goal",
        ),
    ],
    "body_polish": [
        Step(
            kind="single",
            title="Полировка кузова: какая цель?",
            options=[
                ("gloss", "Вернуть блеск/глубину цвета"),
                ("scratches", "Убрать мелкие царапины/паутинку"),
                ("before_sale", "Под продажу авто"),
                ("complex", "Комплексно (и блеск, и царапины)"),
            ],
            hint="💡 Перед керамикой почти всегда делаем подготовку/полировку.",
            store_key="body_polish_goal",
        ),
        Step(
            kind="single",
            title="Кузов сейчас:",
            options=[
                ("washed", "Мою регулярно, но стало тускло"),
                ("swirls", "Есть «паутинка»/микроцарапины"),
                ("chips", "Есть сколы/сильные дефекты"),
                ("idk", "Не знаю, хочу диагностику"),
            ],
            hint="",
            store_key="body_polish_state",
        ),
    ],
    "ceramic": [
        Step(
            kind="single",
            title="Керамика: что важнее?",
            options=[
                ("shine", "Блеск + гидрофоб"),
                ("protection", "Защита от реагентов/грязи"),
                ("easy_wash", "Чтобы легче мыть"),
                ("all", "Всё сразу"),
            ],
            hint="💡 Лучше наносится на подготовленный кузов (полировка/подготовка).",
            store_key="ceramic_priority",
        ),
        Step(
            kind="single",
            title="Керамика: срок, который хочешь получить?",
            options=[
                ("6", "До 6 месяцев"),
                ("12", "Около 1 года"),
                ("24", "До 2 лет"),
                ("idk", "Не знаю — подскажи"),
            ],
            hint="",
            store_key="ceramic_term",
        ),
    ],
    "water_spots": [
        Step(
            kind="single",
            title="Водный камень: где сильнее всего?",
            options=[
                ("front", "Лобовое"),
                ("side", "Боковые"),
                ("rear", "Заднее"),
                ("all", "Везде"),
            ],
            hint="💡 После удаления налёта часто рекомендуем «антидождь» — будет дольше чисто.",
            store_key="water_spots_where",
        ),
        Step(
            kind="single",
            title="Налёт сейчас:",
            options=[
                ("light", "Лёгкий (видно на солнце)"),
                ("mid", "Средний (мешает в дождь/ночью)"),
                ("hard", "Сильный (пятна, разводы, плохо отмывается)"),
                ("idk", "Не знаю"),
            ],
            hint="",
            store_key="water_spots_level",
        ),
    ],
    "anti_rain": [
        Step(
            kind="single",
            title="Антидождь: куда наносим?",
            options=[
                ("windshield", "Только лобовое"),
                ("front_plus", "Лобовое + передние боковые"),
                ("all", "Все стёкла"),
            ],
            hint="💡 Самый заметный эффект — лобовое + передние боковые.",
            store_key="anti_rain_where",
        ),
    ],
    "headlights": [
        Step(
            kind="single",
            title="Фары: что происходит?",
            options=[
                ("yellow", "Пожелтели"),
                ("matte", "Помутнели"),
                ("scratched", "Есть царапины"),
                ("weak", "Светит хуже"),
            ],
            hint="💡 После полировки лучше защитить — дольше держится эффект.",
            store_key="headlights_problem",
        ),
        Step(
            kind="single",
            title="Фары:",
            options=[
                ("halogen", "Галоген"),
                ("led", "LED"),
                ("xenon", "Ксенон"),
                ("idk", "Не знаю"),
            ],
            hint="",
            store_key="headlights_type",
        ),
    ],
    "glass_polish": [
        Step(
            kind="single",
            title="Стекло: что за проблема?",
            options=[
                ("wipers", "Царапины от дворников"),
                ("sand", "Пескоструй"),
                ("spots", "Пятна/налёт"),
                ("fog", "Помутнение/искажение"),
                ("idk", "Не знаю"),
            ],
            hint="💡 Глубокие царапины не всегда уходят «в ноль» — лучше сначала диагностика.",
            store_key="glass_problem",
        ),
        Step(
            kind="single",
            title="Стекло: какое?",
            options=[
                ("windshield", "Лобовое"),
                ("side", "Боковое"),
                ("rear", "Заднее"),
            ],
            hint="",
            store_key="glass_where",
        ),
    ],
    "interior_clean": [
        Step(
            kind="multi",
            title="Химчистка: что делаем? (можно несколько)",
            options=[
                ("express", "Экспресс уборка"),
                ("whole", "Химчистка всего салона"),
                ("seats", "Сиденья"),
                ("ceiling", "Потолок"),
                ("floor", "Пол/ковры"),
                ("doors", "Дверные карты"),
                ("trunk", "Багажник"),
                ("leather_protect", "Чистка кожи + пропитка"),
            ],
            hint="💡 Если есть запах — лучше написать какой (сигареты/сырость/животные).",
            store_key="clean_scope",
        ),
        Step(
            kind="single",
            title="Салон: материал?",
            options=[
                ("fabric", "Ткань"),
                ("leather", "Кожа"),
                ("mix", "Комбинированный"),
                ("idk", "Не знаю"),
            ],
            hint="",
            store_key="clean_material",
        ),
        Step(
            kind="single",
            title="Главная проблема?",
            options=[
                ("stains", "Пятна/разводы"),
                ("odor", "Запах (сигареты/сырость/животные)"),
                ("dust", "Сильно пыльно/грязно"),
                ("kids_pets", "После детей/животных"),
                ("sale", "Под продажу авто"),
                ("idk", "Не знаю / разное"),
            ],
            hint="",
            store_key="clean_problem",
        ),
        Step(
            kind="single",
            title="Срочность?",
            options=[
                ("today", "Сегодня/завтра"),
                ("week", "В течение недели"),
                ("no_rush", "Не срочно"),
            ],
            hint="",
            store_key="clean_urgency",
        ),
    ],
    "engine_wash": [
        Step(
            kind="single",
            title="Мойка мотора: цель?",
            options=[
                ("sale", "Под продажу / презентабельность"),
                ("maintenance", "Для обслуживания (чтобы было чисто)"),
                ("leak_check", "Подозрение на течь / хочу выявить"),
                ("just_clean", "Просто привести в порядок"),
            ],
            hint="💡 Консервация защищает пластик/резину и помогает дольше держать чистоту.",
            store_key="engine_goal",
        ),
        Step(
            kind="single",
            title="Состояние мотора сейчас:",
            options=[
                ("light", "Лёгкая пыль/грязь"),
                ("dirty", "Сильно грязный"),
                ("oily", "Есть масляные следы/подтеки"),
                ("idk", "Не знаю"),
            ],
            hint="",
            store_key="engine_state",
        ),
        Step(
            kind="single",
            title="Консервация после мойки нужна?",
            options=[
                ("yes", "Да, с консервацией"),
                ("no", "Нет, только мойка"),
                ("idk", "Не знаю — подскажи"),
            ],
            hint="",
            store_key="engine_conservation",
        ),
    ],
}

# =======================
# HELPERS
# =======================
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
    if digits.startswith("7") and len(only_digits) == 11:
        return "+7" + only_digits[1:]
    if digits.startswith("+7") and len(only_digits) == 11:
        return "+7" + only_digits[-10:]
    if digits.startswith("+") and len(only_digits) >= 10:
        return digits

    return digits

def humanize_multi(opts: List[Tuple[str, str]], selected: List[str]) -> str:
    labels = {v: t for v, t in opts}
    out = [labels.get(v, v) for v in selected]
    return ", ".join(out) if out else "—"

def build_services_kb(selected: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for key, label in SERVICES.items():
        mark = "✅" if key in selected else "☐"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"svc:toggle:{key}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data="svc:done"),
            InlineKeyboardButton("Сбросить ↩️", callback_data="svc:reset"),
        ]
    )
    rows.append([InlineKeyboardButton("Пройти заново 🔄", callback_data="flow:restart")])
    return InlineKeyboardMarkup(rows)

def build_single_kb(service_key: str, step_idx: int, options: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(title, callback_data=f"step:single:{service_key}:{step_idx}:{val}")]
            for val, title in options]
    rows.append([InlineKeyboardButton("Пройти заново 🔄", callback_data="flow:restart")])
    return InlineKeyboardMarkup(rows)

def build_multi_kb(service_key: str, step_idx: int, options: List[Tuple[str, str]], selected: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for val, title in options:
        mark = "✅" if val in selected else "☐"
        rows.append([InlineKeyboardButton(f"{mark} {title}", callback_data=f"step:multi:{service_key}:{step_idx}:{val}")])
    rows.append(
        [
            InlineKeyboardButton("Готово ✅", callback_data=f"step:multi_done:{service_key}:{step_idx}"),
            InlineKeyboardButton("Сбросить ↩️", callback_data=f"step:multi_reset:{service_key}:{step_idx}"),
        ]
    )
    rows.append([InlineKeyboardButton("Пройти заново 🔄", callback_data="flow:restart")])
    return InlineKeyboardMarkup(rows)

def service_recommendation(service_key: str) -> str:
    if service_key == "ceramic":
        return "💡 Если планируешь керамику — лучше сразу сделать подготовку/полировку, будет заметно круче."
    if service_key == "body_polish":
        return "💡 Если дальше думаешь про керамику — полировка/подготовка почти всегда обязательна."
    if service_key == "interior_clean":
        return "💡 Если есть запах — напиши какой (сигареты/сырость/животные). Так обработка будет точнее."
    if service_key == "water_spots":
        return "💡 После удаления водного камня «антидождь» помогает дольше держать стекло чистым."
    if service_key == "tint":
        return "💡 Для ночной езды чаще выбирают 15–35%. 2–5% — очень темно."
    if service_key == "headlights":
        return "💡 После полировки фар лучше защитить — так эффект держится дольше."
    if service_key == "engine_wash":
        return "💡 Консервация после мойки защищает пластик/резину и делает вид мотора заметно «свежее»."
    return ""

async def safe_edit_or_send(update: Update, text: str, reply_markup=None, parse_mode=None):
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
            return
        except Exception:
            pass
    if update.effective_chat:
        await update.effective_chat.send_message(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def init_flow(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["selected_services"] = []
    context.user_data["service_queue"] = []
    context.user_data["current_service_idx"] = 0
    context.user_data["current_step_idx"] = 0
    context.user_data["answers"] = {}
    context.user_data["multi_buffer"] = {}

# =======================
# LEAD TEMPERATURE
# =======================
def classify_lead(preferred_time: str, selected_services: List[str], contact_method: str) -> Tuple[str, str]:
    """
    Возвращает (температура, причина)
    Простая и понятная логика:
    - горячий: "сегодня/сейчас/в ближайшее время/завтра" или конкретное время + есть телефон
    - тёплый: в течение недели/выходные/несколько услуг/есть керамика+полировка
    - холодный: иначе
    """
    t = (preferred_time or "").lower()

    hot_words = ["сегодня", "сейчас", "прямо", "в ближайшее", "через", "вечером", "утром", "завтра", "после"]
    warm_words = ["на неделе", "в течение недели", "выходн", "в суббот", "в воскрес", "на следующей неделе", "позже"]

    services_set = set(selected_services)
    combo = ("ceramic" in services_set and "body_polish" in services_set)

    score = 0
    reasons = []

    if any(w in t for w in hot_words):
        score += 3
        reasons.append("хочет ближайшее время")
    if any(w in t for w in warm_words):
        score += 2
        reasons.append("планирует на неделе/позже")
    if len(selected_services) >= 2:
        score += 2
        reasons.append("выбрал несколько услуг")
    if combo:
        score += 2
        reasons.append("керамика + подготовка (сильное намерение)")
    if contact_method == "phone":
        score += 2
        reasons.append("оставил телефон")
    else:
        score -= 1
        reasons.append("только Telegram")

    if score >= 6:
        return "🔥 Горячий", ", ".join(reasons[:3])
    if score >= 3:
        return "🟠 Тёплый", ", ".join(reasons[:3])
    return "🔵 Холодный", ", ".join(reasons[:3])

# =======================
# FLOW HANDLERS
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_flow(context)
    await update.message.reply_text(
        "Привет! Я помогу быстро подобрать услуги и записать тебя.\n\nКак тебя зовут?"
    )
    return ASK_NAME

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_flow(context)
    await update.message.reply_text("Ок, начнём заново 🙂\n\nКак тебя зовут?")
    return ASK_NAME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_flow(context)
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    selected = context.user_data.get("selected_services", [])
    await update.message.reply_text(
        "Выбери услуги (можно несколько) и нажми «Готово ✅».",
        reply_markup=build_services_kb(selected),
    )
    return PICK_SERVICES

# =======================
# SERVICES PICKER (callbacks)
# =======================
async def services_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    if data == "flow:restart":
        init_flow(context)
        await safe_edit_or_send(update, "Ок, начнём заново 🙂\n\nКак тебя зовут?")
        return ASK_NAME

    selected: List[str] = context.user_data.get("selected_services", [])

    if data.startswith("svc:toggle:"):
        key = data.split(":", 2)[2]
        if key in selected:
            selected.remove(key)
        else:
            selected.append(key)
        context.user_data["selected_services"] = selected
        await safe_edit_or_send(
            update,
            "Выбери услуги (можно несколько) и нажми «Готово ✅».",
            reply_markup=build_services_kb(selected),
        )
        return PICK_SERVICES

    if data == "svc:reset":
        context.user_data["selected_services"] = []
        await safe_edit_or_send(
            update,
            "Выбери услуги (можно несколько) и нажми «Готово ✅».",
            reply_markup=build_services_kb([]),
        )
        return PICK_SERVICES

    if data == "svc:done":
        if not selected:
            await safe_edit_or_send(
                update,
                "Нужно выбрать хотя бы одну услугу 🙂",
                reply_markup=build_services_kb(selected),
            )
            return PICK_SERVICES

        queue = order_services(selected)
        context.user_data["service_queue"] = queue
        context.user_data["current_service_idx"] = 0
        context.user_data["current_step_idx"] = 0
        context.user_data["answers"] = {k: {} for k in queue}

        await safe_edit_or_send(update, "Отлично! Уточню пару моментов по выбранным услугам 👇")
        return await ask_next_step(update, context)

    return PICK_SERVICES

# =======================
# SERVICE STEPS ENGINE
# =======================
async def ask_next_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    queue: List[str] = context.user_data.get("service_queue", [])
    s_idx: int = context.user_data.get("current_service_idx", 0)
    st_idx: int = context.user_data.get("current_step_idx", 0)

    if s_idx >= len(queue):
        await safe_edit_or_send(
            update,
            "Когда тебе удобно подъехать? Напиши день/время (например: «сегодня после 18:00» или «в пятницу 12:00»).",
            reply_markup=None,
        )
        return ASK_TIME

    service_key = queue[s_idx]
    steps = SERVICE_STEPS.get(service_key, [])

    if st_idx >= len(steps):
        rec = service_recommendation(service_key)
        if rec:
            await safe_edit_or_send(update, rec)
        context.user_data["current_service_idx"] = s_idx + 1
        context.user_data["current_step_idx"] = 0
        return await ask_next_step(update, context)

    step = steps[st_idx]

    header = f"*{SERVICES.get(service_key, service_key)}*\n{step.title}"
    if step.hint:
        header += f"\n\n{step.hint}"

    if step.kind == "single":
        kb = build_single_kb(service_key, st_idx, step.options or [])
        await safe_edit_or_send(update, header, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return SERVICE_FLOW

    if step.kind == "multi":
        key = f"{service_key}:{st_idx}"
        selected = context.user_data.setdefault("multi_buffer", {}).get(key, [])
        kb = build_multi_kb(service_key, st_idx, step.options or [], selected)
        await safe_edit_or_send(update, header, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return SERVICE_FLOW

    await safe_edit_or_send(update, "Что-то пошло не так. Напиши /restart 🙂")
    return ConversationHandler.END

async def steps_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "flow:restart":
        init_flow(context)
        await safe_edit_or_send(update, "Ок, начнём заново 🙂\n\nКак тебя зовут?")
        return ASK_NAME

    # SINGLE: step:single:<service_key>:<step_idx>:<val>
    if data.startswith("step:single:"):
        _, _, service_key, step_idx_str, val = data.split(":", 4)
        step_idx = int(step_idx_str)

        queue: List[str] = context.user_data.get("service_queue", [])
        cur_service = queue[context.user_data.get("current_service_idx", 0)]
        cur_step = context.user_data.get("current_step_idx", 0)

        if service_key != cur_service or step_idx != cur_step:
            return SERVICE_FLOW

        step = SERVICE_STEPS[service_key][step_idx]
        context.user_data["answers"][service_key][step.store_key] = val

        context.user_data["current_step_idx"] = cur_step + 1
        return await ask_next_step(update, context)

    # MULTI toggle: step:multi:<service_key>:<step_idx>:<val>
    if data.startswith("step:multi:"):
        _, _, service_key, step_idx_str, val = data.split(":", 4)
        step_idx = int(step_idx_str)

        queue: List[str] = context.user_data.get("service_queue", [])
        cur_service = queue[context.user_data.get("current_service_idx", 0)]
        cur_step = context.user_data.get("current_step_idx", 0)

        if service_key != cur_service or step_idx != cur_step:
            return SERVICE_FLOW

        key = f"{service_key}:{step_idx}"
        buf = context.user_data.setdefault("multi_buffer", {}).setdefault(key, [])
        if val in buf:
            buf.remove(val)
        else:
            buf.append(val)

        step = SERVICE_STEPS[service_key][step_idx]
        kb = build_multi_kb(service_key, step_idx, step.options or [], buf)
        header = f"*{SERVICES.get(service_key, service_key)}*\n{step.title}"
        if step.hint:
            header += f"\n\n{step.hint}"
        await safe_edit_or_send(update, header, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return SERVICE_FLOW

    # MULTI done: step:multi_done:<service_key>:<step_idx>
    if data.startswith("step:multi_done:"):
        _, _, service_key, step_idx_str = data.split(":", 3)
        step_idx = int(step_idx_str)

        queue: List[str] = context.user_data.get("service_queue", [])
        cur_service = queue[context.user_data.get("current_service_idx", 0)]
        cur_step = context.user_data.get("current_step_idx", 0)

        if service_key != cur_service or step_idx != cur_step:
            return SERVICE_FLOW

        key = f"{service_key}:{step_idx}"
        buf = context.user_data.setdefault("multi_buffer", {}).get(key, [])
        step = SERVICE_STEPS[service_key][step_idx]

        if not buf:
            kb = build_multi_kb(service_key, step_idx, step.options or [], buf)
            header = f"*{SERVICES.get(service_key, service_key)}*\n{step.title}\n\nВыбери хотя бы один вариант 🙂"
            await safe_edit_or_send(update, header, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            return SERVICE_FLOW

        context.user_data["answers"][service_key][step.store_key] = list(buf)
        context.user_data["current_step_idx"] = cur_step + 1
        return await ask_next_step(update, context)

    # MULTI reset: step:multi_reset:<service_key>:<step_idx>
    if data.startswith("step:multi_reset:"):
        _, _, service_key, step_idx_str = data.split(":", 3)
        step_idx = int(step_idx_str)

        queue: List[str] = context.user_data.get("service_queue", [])
        cur_service = queue[context.user_data.get("current_service_idx", 0)]
        cur_step = context.user_data.get("current_step_idx", 0)

        if service_key != cur_service or step_idx != cur_step:
            return SERVICE_FLOW

        key = f"{service_key}:{step_idx}"
        context.user_data.setdefault("multi_buffer", {})[key] = []
        step = SERVICE_STEPS[service_key][step_idx]
        kb = build_multi_kb(service_key, step_idx, step.options or [], [])
        header = f"*{SERVICES.get(service_key, service_key)}*\n{step.title}"
        if step.hint:
            header += f"\n\n{step.hint}"
        await safe_edit_or_send(update, header, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return SERVICE_FLOW

    return SERVICE_FLOW

# =======================
# TIME + CONTACT
# =======================
async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 2:
        await update.message.reply_text("Напиши день/время чуть понятнее 🙂")
        return ASK_TIME

    context.user_data["preferred_time"] = txt

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Отправить контакт ☎️", request_contact=True)],
            [KeyboardButton("Написать номер текстом")],
            [KeyboardButton("Оставлю Telegram, можно сюда")],
            [KeyboardButton("Пройти заново 🔄")],
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
    if (update.message.text or "").strip().lower().startswith("пройти заново"):
        init_flow(context)
        await update.message.reply_text("Ок, начнём заново 🙂\n\nКак тебя зовут?")
        return ASK_NAME

    contact_method = "telegram"
    phone = ""

    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        contact_method = "phone"
    else:
        txt = (update.message.text or "").strip()

        if "телег" in txt.lower() or "сюда" in txt.lower() or "tg" in txt.lower():
            phone = ""
            contact_method = "telegram"
        else:
            phone_norm = normalize_phone(txt)
            if not phone_norm:
                await update.message.reply_text(
                    "Не похоже на номер 🙂\n"
                    "Напиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
                )
                return ASK_CONTACT
            phone = phone_norm
            contact_method = "phone"

    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "(нет username)"

    name = context.user_data.get("name", "")
    preferred_time = context.user_data.get("preferred_time", "")
    selected_services = context.user_data.get("service_queue", [])
    answers = context.user_data.get("answers", {})

    temp, why = classify_lead(preferred_time, selected_services, contact_method)

    lines = []
    for sk in selected_services:
        lines.append(f"• {SERVICES.get(sk, sk)}")
        st = SERVICE_STEPS.get(sk, [])
        a = answers.get(sk, {})
        for step in st:
            if not step.store_key:
                continue
            if step.store_key not in a:
                continue
            val = a[step.store_key]
            if isinstance(val, list):
                rendered = humanize_multi(step.options or [], val)
            else:
                rendered = val
                if step.options:
                    m = {v: t for v, t in step.options}
                    rendered = m.get(val, val)
            lines.append(f"   - {step.title}: {rendered}")

    lead_text = (
        "🔥 *НОВЫЙ ЛИД*\n"
        f"*Температура:* {temp}\n"
        f"*Почему:* {why}\n\n"
        f"*Имя:* {name}\n"
        f"*TG:* {username}\n"
        f"*Услуги:*\n" + "\n".join(lines) + "\n\n"
        f"*Когда удобно:* {preferred_time}\n"
        f"*Контакт:* {phone or 'Telegram'}\n"
    )

    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print("FAILED TO SEND LEAD:", e)
        print(lead_text)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("Пройти заново 🔄")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "✅ Принято! Я передал информацию менеджеру.\n"
        "Он свяжется с тобой в ближайшее время.\n\n"
        "Если хочешь — можешь дописать детали (фото/видео тоже можно).",
        reply_markup=kb,
    )
    return ConversationHandler.END

# =======================
# MAIN
# =======================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("restart", restart_cmd),
        ],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            PICK_SERVICES: [CallbackQueryHandler(services_cb)],
            SERVICE_FLOW: [CallbackQueryHandler(steps_cb)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("restart", restart_cmd),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # Если раньше был webhook — удалить:
    # https://api.telegram.org/bot<TOKEN>/deleteWebhook
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()