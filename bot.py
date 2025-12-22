import os
import re
from datetime import datetime
from dotenv import load_dotenv

from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.constants import ParseMode
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
ADMIN_ID = os.getenv("ADMIN_ID")  # твой Telegram user id
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID not set")
ADMIN_ID = int(ADMIN_ID)

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

    # РФ: 8XXXXXXXXXX -> +7XXXXXXXXXX
    if digits.startswith("8") and len(only_digits) == 11:
        digits = "+7" + only_digits[1:]
    elif digits.startswith("7") and len(only_digits) == 11:
        digits = "+7" + only_digits
    elif digits.startswith("+7") and len(only_digits) == 11:
        digits = "+7" + only_digits[-10:]

    return digits


async def safe_send_admin(app: Application, text: str):
    """Отправка админу с защитой от падений."""
    try:
        await app.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print("ADMIN SEND ERROR:", repr(e))


# --------- handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. "
        "Займёт 1–2 минуты 🙂\n\n"
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
        "Понял. А что больше всего беспокоит прямо сейчас?\n"
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
    # 1) контакт кнопкой
    if update.message.contact and update.message.contact.phone_number:
        phone = normalize_phone(update.message.contact.phone_number) or update.message.contact.phone_number
        context.user_data["phone"] = phone
        context.user_data["contact_method"] = "phone"
    else:
        txt = (update.message.text or "").strip()

        # 2) если пользователь хочет связаться в TG
        if any(w in txt.lower() for w in ["телег", "сюда", "tg", "telegram"]):
            context.user_data["contact_method"] = "telegram"
            context.user_data["phone"] = ""
        else:
            # 3) номер текстом
            phone = normalize_phone(txt)
            if not phone:
                await update.message.reply_text(
                    "Не похоже на номер 🙂\n"
                    "Напиши в формате +7... или 8..., либо нажми «Отправить контакт ☎️»."
                )
                return ASK_CONTACT
            context.user_data["phone"] = phone
            context.user_data["contact_method"] = "phone"

    # --- сбор данных пользователя (ВАЖНО: именно тут, чтобы не было NameError) ---
    user = update.effective_user
    chat = update.effective_chat

    username = f"@{user.username}" if (user and user.username) else "(нет username)"
    user_id = user.id if user else "unknown"
    chat_id = chat.id if chat else "unknown"

    # --- текст лида ---
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    contact_value = context.user_data.get("phone", "") or "Telegram"
    name = context.user_data.get("name", "")
    ctx = context.user_data.get("context", "")
    pain = context.user_data.get("pain", "")
    res = context.user_data.get("result", "")

    lead_text = (
        "🔥 <b>НОВЫЙ ЛИД</b>\n"
        f"🕒 {ts}\n"
        "-----------------\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"💬 TG: {username}\n"
        f"🆔 UserID: <code>{user_id}</code>\n"
        f"🧾 ChatID: <code>{chat_id}</code>\n\n"
        f"🚗 Контекст: {ctx}\n"
        f"😤 Боль: {pain}\n"
        f"✅ Результат: {res}\n\n"
        f"☎️ Контакт: <b>{contact_value}</b>\n"
    )

    # 1) печать в лог Render
    print(lead_text)

    # 2) отправка тебе в Telegram
    await safe_send_admin(context.application, lead_text)

    # ответ клиенту
    await update.message.reply_text(
        "✅ Принято! Я передал информацию менеджеру.\n"
        "Он свяжется с тобой в ближайшее время.\n\n"
        "Если хочешь — можешь дописать детали или отправить фото/видео.",
        reply_markup=None,
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # быстрая команда для проверки что бот жив
    await update.message.reply_text("✅ Я на связи. Напиши /start чтобы начать.")


def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_CONTEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_context)],
            ASK_PAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_pain)],
            ASK_RESULT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_result)],
            ASK_CONTACT: [
                MessageHandler(filters.CONTACT, ask_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("health", health))

    # polling (важно: webhook должен быть удалён)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()