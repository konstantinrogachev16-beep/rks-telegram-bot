import os
import re
import threading
from dotenv import load_dotenv

from flask import Flask

from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# --------- env ----------
load_dotenv()  # локально читает .env, на Render не мешает
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

# Куда слать лиды (тебе)
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "327140660"))

# --------- states ----------
ASK_NAME, ASK_CONTEXT, ASK_PAIN, ASK_RESULT, ASK_CONTACT = range(5)


def normalize_phone(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    digits = re.sub(r"[^\d+]", "", s)

    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) < 10:
        return None

    # РФ приведение 8XXXXXXXXXX -> +7XXXXXXXXXX
    if digits.startswith("8") and len(only_digits) == 11:
        digits = "+7" + only_digits[1:]
    elif digits.startswith("7") and len(only_digits) == 11:
        digits = "+7" + only_digits
    elif digits.startswith("+7") and len(only_digits) == 11:
        digits = "+7" + only_digits[-10:]

    return digits


# --------- Web Service "костыль" для Render ----------
def run_web():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return "OK"

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# --------- handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. "
        "Займёт буквально пару минут, ок? 🙂\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = name
    await update.message.reply_text(
        "Отлично! Расскажи в двух словах про машину и ситуацию.\n"
        "Например: «Camry 2018, хочу освежить внешний вид / есть царапины / стекла в налёте»"
    )
    return ASK_CONTEXT


async def ask_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 3:
        await update.message.reply_text("Чуть подробнее 🙂 Что за машина и что с ней сейчас?")
        return ASK_CONTEXT

    context.user_data["context"] = txt
    await update.message.reply_text(
        "Понял. А что больше всего беспокоит прямо сейчас? "
        "Что раздражает/мешает/не нравится?"
    )
    return ASK_PAIN


async def ask_pain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 3:
        await update.message.reply_text("Опиши одним-двумя предложениями 🙂")
        return ASK_PAIN

    context.user_data["pain"] = txt
    await update.message.reply_text(
        "Ок. А какой результат хочешь получить в идеале?\n"
        "Например: «чтобы блестела как новая», «чистые стёкла без налёта», «без мелких царапин»"
    )
    return ASK_RESULT


async def ask_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if len(txt) < 3:
        await update.message.reply_text("Супер коротко: какой идеальный итог? 🙂")
        return ASK_RESULT

    context.user_data["result"] = txt

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
        "Спасибо, картина ясна 👍\n\n"
        "Чтобы передать всё менеджеру и подобрать лучшее решение, оставь удобный контакт:\n"
        "• нажми «Отправить контакт»\n"
        "• или напиши номер текстом\n"
        "• или просто скажи «можно сюда в Telegram»",
        reply_markup=kb,
    )
    return ASK_CONTACT


async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # контакт кнопкой
    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        context.user_data["phone"] = phone
        context.user_data["contact_method"] = "phone"
    else:
        txt = (update.message.text or "").strip()

        # если пользователь хочет связаться в TG
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

    # --- сбор лида ---
    user = update.effective_user
username = f"@{user.username}" if user and user.username else "(нет username)"
user_id = user.id if user else "unknown"
chat_id = update.effective_chat.id if update.effective_chat else "unknown"

lead_text = (
    "🔥 НОВЫЙ ЛИД\n"
    "-----------------\n"
    f"Имя: {context.user_data.get('name','')}\n"
    f"TG: {username}\n"
    f"UserID: {user_id}\n"
    f"ChatID: {chat_id}\n\n"
    f"Контекст: {context.user_data.get('context','')}\n"
    f"Боль: {context.user_data.get('pain','')}\n"
    f"Результат: {context.user_data.get('result','')}\n\n"
    f"Контакт: {context.user_data.get('phone','') or 'Telegram'}\n"
)