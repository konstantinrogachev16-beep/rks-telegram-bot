import os
import re
import logging
from dotenv import load_dotenv

from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ----------------- logging -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rks-bot")

# ----------------- env -----------------
load_dotenv()  # локально читает .env, на Render не мешает
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID", "327140660")  # по умолчанию твой ID


# ----------------- states -----------------
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


def lead_to_text(user, data: dict) -> str:
    username = f"@{user.username}" if user and user.username else "(нет username)"
    return (
        "🔥 НОВЫЙ ЛИД\n"
        f"Имя: {data.get('name','')}\n"
        f"TG: {username}\n"
        f"Контекст: {data.get('context','')}\n"
        f"Боль: {data.get('pain','')}\n"
        f"Результат: {data.get('result','')}\n"
        f"Контакт: {data.get('phone','') or 'Telegram'}\n"
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


def restart_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("Пройти диагностику заново ✅")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ----------------- handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. "
        "Займёт буквально пару минут, ок? 🙂\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # быстрый перезапуск
    if text.lower().startswith("пройти диагностику"):
        return await start(update, context)

    if len(text) < 2:
        await update.message.reply_text("Напиши имя чуть понятнее 🙂")
        return ASK_NAME

    context.user_data["name"] = text
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

    await update.message.reply_text(
        "Спасибо, картина ясна 👍\n\n"
        "Чтобы передать всё менеджеру и подобрать лучшее решение, оставь удобный контакт:\n"
        "• нажми «Отправить контакт»\n"
        "• или напиши номер текстом\n"
        "• или просто скажи «можно сюда в Telegram»",
        reply_markup=contact_kb(),
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
        if any(x in txt.lower() for x in ["телег", "сюда", "tg", "telegram"]):
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

    # сформировать лид
    user = update.effective_user
    lead_text = lead_to_text(user, context.user_data)

    # лог в консоль Render
    logger.info("\n" + lead_text)

    # отправка менеджеру (тебе)
    try:
        await context.bot.send_message(chat_id=int(MANAGER_CHAT_ID), text=lead_text)
    except Exception as e:
        logger.exception("Failed to send lead to manager: %s", e)

    await update.message.reply_text(
        "✅ Принято! Я передал информацию менеджеру.\n"
        "Он свяжется с тобой в ближайшее время.\n\n"
        "Если хочешь — можешь прямо сейчас дописать любые детали (фото/видео тоже можно).",
        reply_markup=restart_kb(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, остановил. Если нужно — напиши /start 🙂")
    return ConversationHandler.END


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Я на связи. Напиши /start чтобы пройти мини-диагностику 🙂")


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
    app.add_handler(CommandHandler("ping", ping))

    # polling (важно: должен быть только один запущенный экземпляр)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()