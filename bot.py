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

def load_config() ->