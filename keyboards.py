
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

SEGMENTS = [
    ("😕 Уставшая (тусклая/матовая)", "SEG_TIRED"),
    ("✨ Новая (хочу сохранить)", "SEG_NEW"),
    ("🚗 Много езжу (трасса/реагенты)", "SEG_MILEAGE"),
    ("😬 Стыдно за салон/вид", "SEG_SHAME"),
    ("💸 На продажу", "SEG_SELL"),
]

PAINS = [
    ("Тусклый кузов / нет блеска", "PAIN_DULL"),
    ("Царапины / паутинка", "PAIN_SCRATCH"),
    ("Салон грязный / запах", "PAIN_INTERIOR"),
    ("Мутные фары", "PAIN_LIGHTS"),
    ("Стекла в царапинах", "PAIN_GLASS"),
    ("Водный камень / пятна", "PAIN_WATERSPOTS"),
    ("Всё сразу 😤", "PAIN_ALL"),
]

SERVICES = [
    ("Полировка кузова", "SRV_POLISH"),
    ("Защита (керамика/воск/стекло)", "SRV_PROTECT"),
    ("Химчистка салона / кожа", "SRV_CLEAN"),
    ("Тонировка по ГОСТ", "SRV_TINT"),
    ("Фары (восстановление)", "SRV_HEADLIGHTS"),
    ("Стекла (шлифовка/полировка)", "SRV_GLASS"),
    ("Водный камень + антидождь", "SRV_WATER_ANTIRAIN"),
]

READY = [
    ("Сегодня–завтра", "READY_NOW"),
    ("В течение недели", "READY_WEEK"),
    ("В течение месяца", "READY_MONTH"),
    ("Пока просто смотрю", "READY_LOOK"),
]

CONTACT_METHODS = [
    ("Звонок", "CALL"),
    ("WhatsApp", "WA"),
    ("Telegram", "TG"),
]


def start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать диагностику", callback_data="START_FLOW")]
        ]
    )


def segments_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for text, code in SEGMENTS:
        kb.add(InlineKeyboardButton(text=text, callback_data=f"SEG:{code}"))
    return kb


def pains_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for text, code in PAINS:
        kb.add(InlineKeyboardButton(text=text, callback_data=f"PAIN:{code}"))
    return kb


def services_kb(selected: set) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for text, code in SERVICES:
        mark = "✅ " if code in selected else ""
        kb.add(InlineKeyboardButton(text=f"{mark}{text}", callback_data=f"SRV:{code}"))
    kb.add(InlineKeyboardButton(text="Готово ✅", callback_data="SRV:DONE"))
    return kb


def ready_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for text, code in READY:
        kb.add(InlineKeyboardButton(text=text, callback_data=f"READY:{code}"))
    return kb


def phone_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить контакт", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def contact_method_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for text, code in CONTACT_METHODS:
        kb.add(InlineKeyboardButton(text=text, callback_data=f"CM:{code}"))
    return kb