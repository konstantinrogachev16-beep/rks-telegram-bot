import os
import re
import asyncio
from datetime import datetime
from threading import Thread
from typing import Optional, Set, List, Tuple

from flask import Flask

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardRemove


# =========================
# WEB (Render needs PORT)
# =========================
app = Flask(__name__)

@app.get("/")
def home():
    return "OK", 200

def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

Thread(target=run_web, daemon=True).start()


# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# =========================
# PLACEHOLDERS (если у тебя они уже есть в других файлах — удали этот блок)
# =========================

# --- mappings (пример, замени своими данными если они есть) ---
SEGMENTS: List[Tuple[str, str]] = []
PAINS: List[Tuple[str, str]] = []
READY: List[Tuple[str, str]] = []
CONTACT_METHODS: List[Tuple[str, str]] = []
SERVICES: List[Tuple[str, str]] = []

# --- keyboards (заглушки, если у тебя уже есть свои — удали эти и импортируй свои) ---
def start_kb():
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Начать ✅", callback_data="START_FLOW")]
    ])
    return kb

def segments_kb():
    # тут должны быть кнопки с callback_data вида "SEG:XXXX"
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Пример сегмента", callback_data="SEG:EXAMPLE")]
    ])

def pains_kb():
    # callback_data вида "PAIN:XXXX"
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Пример боли", callback_data="PAIN:EXAMPLE")]
    ])

def services_kb(selected: Set[str]):
    # callback_data вида "SRV:XXXX" + "SRV:DONE"
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Тонировка", callback_data="SRV:TON")],
        [types.InlineKeyboardButton(text="Полировка", callback_data="SRV:POL")],
        [types.InlineKeyboardButton(text="Готово ✅", callback_data="SRV:DONE")],
    ])

def ready_kb():
    # callback_data вида "READY:READY_NOW" etc
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Сейчас", callback_data="READY:READY_NOW")],
        [types.InlineKeyboardButton(text="На этой неделе", callback_data="READY:READY_WEEK")],
        [types.InlineKeyboardButton(text="Позже", callback_data="READY:READY_LATER")],
    ])

def phone_request_kb():
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="📲 Отправить контакт", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return kb

def contact_method_kb():
    # callback_data вида "CM:XXXX"
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Telegram", callback_data="CM:TG")],
        [types.InlineKeyboardButton(text="Телефон", callback_data="CM:PHONE")],
        [types.InlineKeyboardButton(text="Instagram", callback_data="CM:IG")],
    ])

# --- db / managers (заглушки) ---
def init_db():
    return

def save_lead(payload: dict) -> int:
    # верни id лида
    return int(datetime.utcnow().timestamp())

def list_manager_ids() -> List[int]:
    return []

def add_manager(tg_user_id: int, tg_username: Optional[str], name: str):
    return

def remove_manager(tg_user_id: int):
    return

class Cfg:
    bot_token: str = TOKEN
    manager_password: str = os.getenv("MANAGER_PASSWORD", "1234")

def load_config() -> Cfg:
    return Cfg()


# =========================
# HELPERS
# =========================
def code_to_text(code: str, mapping: List[Tuple[str, str]]) -> str:
    for text, c in mapping:
        if c == code:
            return text
    return code

def normalize_phone(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)
    if digits.startswith("8") and len(re.sub(r"\D", "", digits)) == 11:
        digits = "+7" + digits[1:]
    if digits.startswith("7") and len(re.sub(r"\D", "", digits)) == 11:
        digits = "+7" + digits[1:]
    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) < 10:
        return None
    return digits


# =========================
# FSM STATES
# =========================
class LeadForm(StatesGroup):
    name = State()
    car = State()
    segment = State()
    pain = State()
    services = State()
    ready_time = State()
    phone = State()
    contact_method = State()

class ManagerAuth(StatesGroup):
    password = State()


# =========================
# INIT
# =========================
cfg = load_config()
init_db()


# =========================
# START / HELP
# =========================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "Привет! Я помогу быстро понять, что лучше сделать с машиной.\n\n"
        "Нажми кнопку ниже — пройдём мини-диагностику за 1 минуту 👇"
    )
    await message.answer(text, reply_markup=start_kb())

@dp.message(Command("manager"))
async def cmd_manager(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(ManagerAuth.password)
    await message.answer(
        "Введи пароль менеджера (одним сообщением):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(Command("unmanager"))
async def cmd_unmanager(message: types.Message):
    remove_manager(message.from_user.id)
    await message.answer("Ок, ты удалён из менеджеров ✅")


# =========================
# MANAGER AUTH
# =========================
@dp.message(ManagerAuth.password)
async def manager_password(message: types.Message, state: FSMContext):
    pwd = (message.text or "").strip()
    if pwd != cfg.manager_password:
        await message.answer("Пароль неверный ❌ Попробуй ещё раз или напиши /start")
        return

    add_manager(
        tg_user_id=message.from_user.id,
        tg_username=message.from_user.username,
        name=message.from_user.full_name,
    )
    await state.clear()
    await message.answer("✅ Ты добавлен как менеджер. Теперь тебе будут приходить лиды в личку.")


# =========================
# FLOW START
# =========================
@dp.callback_query(lambda c: c.data == "START_FLOW")
async def start_flow(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    await state.set_state(LeadForm.name)
    await state.update_data(
        tg_user_id=call.from_user.id,
        tg_username=call.from_user.username,
        source="telegram_bot",
        created_at=datetime.utcnow().isoformat(),
        services_selected=set(),
    )

    await call.message.answer("Как тебя зовут?", reply_markup=ReplyKeyboardRemove())


# =========================
# NAME
# =========================
@dp.message(LeadForm.name)
async def step_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Напиши имя чуть понятнее 🙂")
        return

    await state.update_data(name=name)
    await state.set_state(LeadForm.car)
    await message.answer("Какая машина? (марка/модель)")


# =========================
# CAR
# =========================
@dp.message(LeadForm.car)
async def step_car(message: types.Message, state: FSMContext):
    car = (message.text or "").strip()
    if len(car) < 2:
        await message.answer("Напиши марку/модель (например: Camry / Solaris)")
        return

    await state.update_data(car=car)
    await state.set_state(LeadForm.segment)
    await message.answer("Что ближе по ситуации?", reply_markup=segments_kb())


# =========================
# SEGMENT
# =========================
@dp.callback_query(lambda c: c.data.startswith("SEG:"), LeadForm.segment)
async def step_segment(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(segment_trigger=code)

    await state.set_state(LeadForm.pain)
    await call.message.answer("Что больше всего беспокоит?", reply_markup=pains_kb())


# =========================
# PAIN
# =========================
@dp.callback_query(lambda c: c.data.startswith("PAIN:"), LeadForm.pain)
async def step_pain(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(pain_main=code)

    await state.set_state(LeadForm.services)
    data = await state.get_data()
    selected: Set[str] = set(data.get("services_selected") or set())

    await call.message.answer(
        "Какие услуги интересуют? (можно выбрать несколько, потом нажми «Готово ✅»)",
        reply_markup=services_kb(selected)
    )


# =========================
# SERVICES (multi)
# =========================
@dp.callback_query(lambda c: c.data.startswith("SRV:"), LeadForm.services)
async def step_services(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    payload = call.data.split(":", 1)[1]

    data = await state.get_data()
    selected: Set[str] = set(data.get("services_selected") or set())

    if payload == "DONE":
        if not selected:
            await call.message.answer("Выбери хотя бы одну услугу 🙂", reply_markup=services_kb(selected))
            return

        await state.update_data(services_selected=selected)
        await state.set_state(LeadForm.ready_time)
        await call.message.answer("Когда планируешь?", reply_markup=ready_kb())
        return

    if payload in selected:
        selected.remove(payload)
    else:
        selected.add(payload)

    await state.update_data(services_selected=selected)
    try:
        await call.message.edit_reply_markup(reply_markup=services_kb(selected))
    except Exception:
        pass


# =========================
# READY TIME
# =========================
@dp.callback_query(lambda c: c.data.startswith("READY:"), LeadForm.ready_time)
async def step_ready(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(ready_time=code)

    lead_temp = "cold"
    if code == "READY_NOW":
        lead_temp = "hot"
    elif code == "READY_WEEK":
        lead_temp = "warm"
    await state.update_data(lead_temp=lead_temp)

    await state.set_state(LeadForm.phone)
    await call.message.answer(
        "Оставь номер телефона — и я передам заявку менеджеру.\n\n"
        "Можно нажать кнопку «Отправить контакт» или написать номер текстом.",
        reply_markup=phone_request_kb()
    )


# =========================
# PHONE
# =========================
@dp.message(LeadForm.phone)
async def step_phone_any(message: types.Message, state: FSMContext):
    phone = None

    if message.contact and message.contact.phone_number:
        phone = message.contact.phone_number
    else:
        phone = message.text

    phone_norm = normalize_phone(phone or "")
    if not phone_norm:
        await message.answer("Не похоже на номер. Напиши ещё раз или нажми «Отправить контакт».")
        return

    await state.update_data(phone=phone_norm)
    await state.set_state(LeadForm.contact_method)
    await message.answer("Как удобнее связаться?", reply_markup=contact_method_kb())


# =========================
# CONTACT METHOD + SAVE
# =========================
@dp.callback_query(lambda c: c.data.startswith("CM:"), LeadForm.contact_method)
async def step_contact_method(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(contact_method=code)

    data = await state.get_data()

    segment_text = code_to_text(data.get("segment_trigger", ""), SEGMENTS)
    pain_text = code_to_text(data.get("pain_main", ""), PAINS)
    ready_text = code_to_text(data.get("ready_time", ""), READY)
    contact_text = code_to_text(data.get("contact_method", ""), CONTACT_METHODS)

    selected_codes: Set[str] = set(data.get("services_selected") or set())
    services_texts = [code_to_text(c, SERVICES) for c in selected_codes]
    services_joined = ", ".join(services_texts)

    lead_payload = {
        "created_at": data.get("created_at"),
        "tg_user_id": data.get("tg_user_id"),
        "tg_username": data.get("tg_username"),
        "name": data.get("name"),
        "phone": data.get("phone"),
        "car": data.get("car"),
        "segment_trigger": segment_text,
        "pain_main": pain_text,
        "services_interest": services_joined,
        "ready_time": ready_text,
        "lead_temp": data.get("lead_temp"),
        "contact_method": contact_text,
        "comment_free": "",
        "source": data.get("source"),
    }

    lead_id = save_lead(lead_payload)

    mgr_ids = list_manager_ids()

    base_msg = (
        "🔥 <b>Новый лид RKS Studio</b>\n"
        f"ID: <code>{lead_id}</code>\n"
        f"Имя: <b>{lead_payload['name']}</b>\n"
        f"Тел: <b>{lead_payload['phone']}</b>\n"
        f"Авто: <b>{lead_payload['car']}</b>\n"
        f"Сегмент: {lead_payload['segment_trigger']}\n"
        f"Боль: {lead_payload['pain_main']}\n"
        f"Интерес: {lead_payload['services_interest']}\n"
        f"Срок: {lead_payload['ready_time']}\n"
        f"Связь: {lead_payload['contact_method']}\n"
        f"Температура: <b>{lead_payload['lead_temp']}</b>\n"
    )
    tg_line = f"TG: @{lead_payload['tg_username']}\n" if lead_payload.get("tg_username") else ""
    manager_msg = base_msg + tg_line

    if mgr_ids:
        for mid in mgr_ids:
            try:
                await bot.send_message(mid, manager_msg)
            except Exception:
                pass

    await call.message.answer(
        "✅ Заявка отправлена! Менеджер свяжется с тобой в ближайшее время.\n"
        "Если нужно срочно — напиши прямо сюда в чат.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()


# =========================
# MAIN
# =========================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())