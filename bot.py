import re
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import load_config
from db import init_db, save_lead, add_manager, remove_manager, list_managers, list_manager_ids
from states import LeadForm, ManagerAuth
from keyboards import (
    start_kb, segments_kb, pains_kb, services_kb,
    ready_kb, phone_request_kb, contact_method_kb,
    SEGMENTS, PAINS, SERVICES
)

def code_to_text(code: str, mapping: list[tuple[str, str]]) -> str:
    for text, c in mapping:
        if c == code:
            return text
    return code

def calc_temp(ready_code: str) -> str:
    if ready_code in ("READY_NOW", "READY_WEEK"):
        return "HOT"
    if ready_code == "READY_MONTH":
        return "WARM"
    return "COLD"

def managers_prefix(temp: str) -> str:
    return {"HOT": "🔥🔥🔥", "WARM": "🟡", "COLD": "⚪️"}.get(temp, "⚪️")

def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D+", "", raw)
    return digits if 10 <= len(digits) <= 12 else raw.strip()

def final_message_by_segment(seg_code: str, name: str) -> str:
    if seg_code == "SEG_TIRED":
        return (f"{name}, по ответам видно: машина “стареет” не из-за лет, "
                f"а из-за микроцарапин и неправильных моек.\n\n"
                f"Оптимально: восстановительная полировка + защита, чтобы блеск держался.\n"
                f"Напишем тебе и предложим 1–2 варианта без лишних услуг 👌")
    if seg_code == "SEG_NEW":
        return (f"{name}, если машина новая — самое умное сейчас защитить кузов, "
                f"чтобы сохранить эффект “как с салона”.\n\n"
                f"Подберём керамику/стекло или воск под бюджет. Напишем тебе 👌")
    if seg_code == "SEG_MILEAGE":
        return (f"{name}, при трассах и реагентах кузов убивается быстрее — потом это дороже.\n\n"
                f"Лучше остановить процесс сейчас: защита + восстановление по факту. Напишем тебе 👌")
    if seg_code == "SEG_SHAME":
        return (f"{name}, понимаю. Когда садишься в авто и внутри “не то” — это бесит каждый день.\n\n"
                f"Обычно решает химчистка + восстановление деталей (по факту). Напишем и подскажем 👌")
    if seg_code == "SEG_SELL":
        return (f"{name}, перед продажей внешний вид = деньги и скорость продажи.\n\n"
                f"Полировка + быстрый защитный состав + фары/химчистка по ситуации дают максимум эффекта. Напишем 👌")
    return f"{name}, спасибо! Мы свяжемся и подскажем лучший вариант 👌"

async def send_lead_to_managers_dm(bot: Bot, lead: dict) -> int:
    """
    Рассылает лид всем менеджерам из БД в личку.
    Возвращает кол-во успешных доставок.
    """
    manager_ids = list_manager_ids()

    prefix = managers_prefix(lead["lead_temp"])
    text = (
        f"{prefix} Новый лид RKS Studio\n"
        f"Имя: {lead.get('name')}\n"
        f"Тел: {lead.get('phone')}\n"
        f"TG: @{lead.get('tg_username') or '-'}\n"
        f"Авто: {lead.get('car')}\n"
        f"Сегмент: {lead.get('segment_trigger')}\n"
        f"Боль: {lead.get('pain_main')}\n"
        f"Интерес: {lead.get('services_interest')}\n"
        f"Срок: {lead.get('ready_time')} → {lead.get('lead_temp')}\n"
        f"Связь: {lead.get('contact_method')}\n"
    )

    delivered = 0
    for uid in manager_ids:
        try:
            await bot.send_message(uid, text)
            delivered += 1
        except TelegramForbiddenError:
            # менеджер не нажал Start / запретил писать
            pass
        except TelegramBadRequest:
            pass

    return delivered

def build_lead_dict(user: Message, data: dict) -> dict:
    seg_code = data.get("segment_trigger_code")
    pain_code = data.get("pain_main_code")
    srv_codes = data.get("services_interest_codes", set())

    segment_text = code_to_text(seg_code, SEGMENTS)
    pain_text = code_to_text(pain_code, PAINS)

    srv_texts = []
    for c in srv_codes:
        srv_texts.append(code_to_text(c, SERVICES))
    srv_texts = sorted(srv_texts)

    ready_code = data.get("ready_time_code")
    ready_text = {
        "READY_NOW": "Сегодня–завтра",
        "READY_WEEK": "В течение недели",
        "READY_MONTH": "В течение месяца",
        "READY_LOOK": "Пока просто смотрю",
    }.get(ready_code, ready_code)

    temp = calc_temp(ready_code)

    cm_code = data.get("contact_method_code")
    cm_text = {"CALL": "Звонок", "WA": "WhatsApp", "TG": "Telegram"}.get(cm_code, cm_code)

    return {
        "created_at": datetime.utcnow().isoformat(),
        "tg_user_id": user.from_user.id,
        "tg_username": user.from_user.username,
        "name": data.get("name"),
        "phone": data.get("phone"),
        "car": data.get("car"),
        "segment_trigger": segment_text,
        "pain_main": pain_text,
        "services_interest": ", ".join(srv_texts) if srv_texts else "-",
        "ready_time": ready_text,
        "lead_temp": temp,
        "contact_method": cm_text,
        "comment_free": None,
        "source": "telegram_bot",
    }

async def main():
    cfg = load_config()
    init_db()

    bot = Bot(cfg.bot_token)
    dp = Dispatcher()

    # ====== START ======
    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        text = (
            "Привет 👋 Я помощник RKS Studio.\n"
            "За 1 минуту помогу понять, что тебе реально нужно, чтобы машина снова выглядела достойно.\n"
            "Ответь на 6 вопросов — в конце дам рекомендацию 👇"
        )
        await message.answer(text, reply_markup=start_kb())

    # ====== UTIL COMMANDS ======
    @dp.message(F.text == "/id")
    async def my_id(message: Message):
        await message.answer(f"Твой user_id: {message.from_user.id}")

    # ====== MANAGER AUTH ======
    @dp.message(F.text == "/manager")
    async def manager_start(message: Message, state: FSMContext):
        await state.set_state(ManagerAuth.password)
        await message.answer("Введи пароль менеджера:")

    @dp.message(ManagerAuth.password)
    async def manager_password(message: Message, state: FSMContext):
        pwd = message.text.strip()
        if pwd != cfg.manager_password:
            await message.answer("Пароль неверный ❌ Попробуй ещё раз или напиши /manager заново.")
            return

        add_manager(
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
            name=message.from_user.full_name
        )
        await state.clear()
        await message.answer("✅ Ты добавлен как менеджер. Теперь будешь получать лиды в личку.")

    @dp.message(F.text == "/unmanager")
    async def manager_remove_cmd(message: Message):
        remove_manager(message.from_user.id)
        await message.answer("Ок, убрал тебя из менеджеров. Лиды больше не будут приходить.")

    @dp.message(F.text == "/managers")
    async def managers_list_cmd(message: Message):
        ms = list_managers()
        if not ms:
            await message.answer("Менеджеров пока нет. Пусть нажмут /manager и введут пароль.")
            return
        lines = []
        for m in ms:
            u = f"@{m['tg_username']}" if m.get("tg_username") else "-"
            lines.append(f"{m['tg_user_id']} | {u} | {m.get('name') or '-'}")
        await message.answer("Менеджеры:\n" + "\n".join(lines))

    # ====== CLIENT FLOW ======
    @dp.callback_query(F.data == "START_FLOW")
    async def start_flow(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.clear()
        await state.set_state(LeadForm.name)
        await cb.message.answer("Как могу к тебе обращаться?")

    @dp.message(LeadForm.name)
    async def get_name(message: Message, state: FSMContext):
        name = (message.text or "").strip()[:40]
        if not name:
            await message.answer("Напиши, пожалуйста, имя текстом 🙂")
            return
        await state.update_data(name=name)
        await state.set_state(LeadForm.car)
        await message.answer(f"{name}, напиши марку и год авто.\nПример: Camry 2019")

    @dp.message(LeadForm.car)
    async def get_car(message: Message, state: FSMContext):
        car = (message.text or "").strip()[:80]
        if not car:
            await message.answer("Напиши марку и год авто текстом 🙂")
            return
        await state.update_data(car=car)
        await state.set_state(LeadForm.segment)
        await message.answer("Что больше всего про твою машину сейчас?", reply_markup=segments_kb())

    @dp.callback_query(LeadForm.segment, F.data.startswith("SEG:"))
    async def pick_segment(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        seg_code = cb.data.split(":", 1)[1]
        await state.update_data(segment_trigger_code=seg_code)
        await state.set_state(LeadForm.pain)
        await cb.message.answer("Что напрягает сильнее всего?", reply_markup=pains_kb())

    @dp.callback_query(LeadForm.pain, F.data.startswith("PAIN:"))
    async def pick_pain(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        pain_code = cb.data.split(":", 1)[1]
        await state.update_data(pain_main_code=pain_code)
        await state.update_data(services_interest_codes=set())
        await state.set_state(LeadForm.services)

        data = await state.get_data()
        selected = data.get("services_interest_codes", set())
        await cb.message.answer(
            "Что хочешь сделать в первую очередь? (можно выбрать несколько)",
            reply_markup=services_kb(selected)
        )

    @dp.callback_query(LeadForm.services, F.data.startswith("SRV:"))
    async def pick_service(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        code = cb.data.split(":", 1)[1]
        data = await state.get_data()
        selected: set[str] = set(data.get("services_interest_codes", set()))

        if code == "DONE":
            await state.update_data(services_interest_codes=selected)
            await state.set_state(LeadForm.ready_time)
            await cb.message.answer("Когда хочешь решить?", reply_markup=ready_kb())
            return

        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)

        await state.update_data(services_interest_codes=selected)
        await cb.message.edit_reply_markup(reply_markup=services_kb(selected))

    @dp.callback_query(LeadForm.ready_time, F.data.startswith("READY:"))
    async def pick_ready(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        ready_code = cb.data.split(":", 1)[1]
        await state.update_data(ready_time_code=ready_code)
        await state.set_state(LeadForm.phone)

        await cb.message.answer(
            "Оставь номер — мы не навязываем, просто подскажем лучший вариант под твою ситуацию и цену по факту 👇",
            reply_markup=phone_request_kb()
        )
        await cb.message.answer("Можешь нажать кнопку «Отправить контакт» или ввести номер вручную.")

    @dp.message(LeadForm.phone, F.contact)
    async def phone_contact(message: Message, state: FSMContext):
        phone = message.contact.phone_number
        await state.update_data(phone=normalize_phone(phone))
        await state.set_state(LeadForm.contact_method)

        await message.answer("Как удобнее связаться?", reply_markup=ReplyKeyboardRemove())
        await message.answer("Выбери способ связи:", reply_markup=contact_method_kb())

    @dp.message(LeadForm.phone)
    async def phone_text(message: Message, state: FSMContext):
        phone_raw = (message.text or "").strip()
        if not phone_raw:
            await message.answer("Введи номер текстом или нажми «Отправить контакт» 🙂")
            return

        await state.update_data(phone=normalize_phone(phone_raw))
        await state.set_state(LeadForm.contact_method)

        await message.answer("Как удобнее связаться?", reply_markup=ReplyKeyboardRemove())
        await message.answer("Выбери способ связи:", reply_markup=contact_method_kb())

    @dp.callback_query(LeadForm.contact_method, F.data.startswith("CM:"))
    async def pick_contact_method(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        cm_code = cb.data.split(":", 1)[1]
        await state.update_data(contact_method_code=cm_code)

        data = await state.get_data()
        lead = build_lead_dict(cb.message, data)

        # Сохраняем лид
        save_lead(lead)

        # Отправляем менеджерам в личку
        delivered = await send_lead_to_managers_dm(cb.bot, lead)

        # Финал пользователю
        seg_code = data.get("segment_trigger_code")
        name = data.get("name") or "Друг"
        await cb.message.answer(final_message_by_segment(seg_code, name))

        if delivered == 0:
            await cb.message.answer("⚠️ Сейчас менеджеры не подключены к боту. Мы всё равно увидим заявку и свяжемся 👌")

        await cb.message.answer("Спасибо! Если хочешь — одним сообщением уточни, что именно беспокоит, и мы точнее подберём вариант.")
        await state.clear()

    @dp.message(F.text == "/restart")
    async def restart(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Ок, перезапускаю 👌", reply_markup=ReplyKeyboardRemove())
        await cmd_start(message, state)

    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())