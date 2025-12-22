
import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("Привет! Я задам пару вопросов, чтобы понять твою ситуацию и быть максимально полезным. Займёт буквально пару минут, ок? 🙂")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
# --- helpers ---
def code_to_text(code: str, mapping: list[tuple[str, str]]) -> str:
    for text, c in mapping:
        if c == code:
            return text
    return code

def normalize_phone(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    # allow +7..., 8..., digits, spaces, dashes
    digits = re.sub(r"[^\d+]", "", s)
    # if starts with 8 and length 11 -> +7
    if digits.startswith("8") and len(re.sub(r"\D", "", digits)) == 11:
        digits = "+7" + digits[1:]
    # if starts with 7 and length 11 -> +7
    if digits.startswith("7") and len(re.sub(r"\D", "", digits)) == 11:
        digits = "+7" + digits[1:]
    # basic check: at least 10 digits
    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) < 10:
        return None
    # ensure has + for international, but it's ok without
    return digits

# --- init ---
cfg = load_config()
bot = Bot(token=cfg.bot_token, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

init_db()

# --- start / help ---
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    text = (
        "Привет! Я помогу быстро понять, что лучше сделать с машиной.\n\n"
        "Нажми кнопку ниже — пройдём мини-диагностику за 1 минуту 👇"
    )
    await message.answer(text, reply_markup=start_kb())

@dp.message_handler(commands=["manager"])
async def cmd_manager(message: types.Message, state: FSMContext):
    await state.finish()
    await ManagerAuth.password.set()
    await message.answer(
        "Введи пароль менеджера (одним сообщением):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message_handler(commands=["unmanager"])
async def cmd_unmanager(message: types.Message):
    remove_manager(message.from_user.id)
    await message.answer("Ок, ты удалён из менеджеров ✅")

# --- manager auth ---
@dp.message_handler(state=ManagerAuth.password, content_types=types.ContentTypes.TEXT)
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
    await state.finish()
    await message.answer("✅ Ты добавлен как менеджер. Теперь тебе будут приходить лиды в личку.")

# --- flow start ---
@dp.callback_query_handler(lambda c: c.data == "START_FLOW")
async def start_flow(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.finish()

    await LeadForm.name.set()
    await state.update_data(
        tg_user_id=call.from_user.id,
        tg_username=call.from_user.username,
        source="telegram_bot",
        created_at=datetime.utcnow().isoformat(),
        services_selected=set(),
    )

    await call.message.answer(
        "Как тебя зовут?",
        reply_markup=ReplyKeyboardRemove()
    )

# --- name ---
@dp.message_handler(state=LeadForm.name, content_types=types.ContentTypes.TEXT)
async def step_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Напиши имя чуть понятнее 🙂")
        return

    await state.update_data(name=name)
    await LeadForm.car.set()
    await message.answer("Какая машина? (марка/модель)")

# --- car ---
@dp.message_handler(state=LeadForm.car, content_types=types.ContentTypes.TEXT)
async def step_car(message: types.Message, state: FSMContext):
    car = (message.text or "").strip()
    if len(car) < 2:
        await message.answer("Напиши марку/модель (например: Camry / Solaris)")
        return

    await state.update_data(car=car)
    await LeadForm.segment.set()
    await message.answer("Что ближе по ситуации?", reply_markup=segments_kb())

# --- segment ---
@dp.callback_query_handler(lambda c: c.data.startswith("SEG:"), state=LeadForm.segment)
async def step_segment(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(segment_trigger=code)

    await LeadForm.pain.set()
    await call.message.answer("Что больше всего беспокоит?", reply_markup=pains_kb())

# --- pain ---
@dp.callback_query_handler(lambda c: c.data.startswith("PAIN:"), state=LeadForm.pain)
async def step_pain(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(pain_main=code)

    await LeadForm.services.set()
    data = await state.get_data()
    selected: Set[str] = set(data.get("services_selected") or set())
    await call.message.answer(
        "Какие услуги интересуют? (можно выбрать несколько, потом нажми «Готово ✅»)",
        reply_markup=services_kb(selected)
    )

# --- services (multi) ---
@dp.callback_query_handler(lambda c: c.data.startswith("SRV:"), state=LeadForm.services)
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
        await LeadForm.ready_time.set()
        await call.message.answer("Когда планируешь?", reply_markup=ready_kb())
        return

    # toggle
    if payload in selected:
        selected.remove(payload)
    else:
        selected.add(payload)

    await state.update_data(services_selected=selected)
    await call.message.edit_reply_markup(reply_markup=services_kb(selected))

# --- ready time ---
@dp.callback_query_handler(lambda c: c.data.startswith("READY:"), state=LeadForm.ready_time)
async def step_ready(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(ready_time=code)

    # temperature (hot/warm/cold) simple
    lead_temp = "cold"
    if code == "READY_NOW":
        lead_temp = "hot"
    elif code == "READY_WEEK":
        lead_temp = "warm"
    await state.update_data(lead_temp=lead_temp)

    await LeadForm.phone.set()
    await call.message.answer(
        "Оставь номер телефона — и я передам заявку менеджеру.\n\n"
        "Можно нажать кнопку «Отправить контакт» или написать номер текстом.",
        reply_markup=phone_request_kb()
    )

# --- phone ---
@dp.message_handler(state=LeadForm.phone, content_types=types.ContentTypes.CONTACT)
async def step_phone_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    phone_norm = normalize_phone(phone) or phone
    await state.update_data(phone=phone_norm)
    await LeadForm.contact_method.set()
    await message.answer("Как удобнее связаться?", reply_markup=contact_method_kb())

@dp.message_handler(state=LeadForm.phone, content_types=types.ContentTypes.TEXT)
async def step_phone_text(message: types.Message, state: FSMContext):
    phone_norm = normalize_phone(message.text or "")
    if not phone_norm:
        await message.answer("Не похоже на номер. Напиши ещё раз или нажми «Отправить контакт».")
        return

    await state.update_data(phone=phone_norm)
    await LeadForm.contact_method.set()
    await message.answer("Как удобнее связаться?", reply_markup=contact_method_kb())

# --- contact method ---
@dp.callback_query_handler(lambda c: c.data.startswith("CM:"), state=LeadForm.contact_method)
async def step_contact_method(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await state.update_data(contact_method=code)

    data = await state.get_data()

    # prepare lead payload
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

    # Notify managers
    mgr_ids = list_manager_ids()
    manager_msg = (
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
        f"TG: @{lead_payload['tg_username']}" if lead_payload.get("tg_username") else ""
    )

    if mgr_ids:
        for mid in mgr_ids:
            try:
                await bot.send_message(mid, manager_msg)
            except Exception:
                pass

    # final to client
    await call.message.answer(
        "✅ Заявка отправлена! Менеджер свяжется с тобой в ближайшее время.\n"
        "Если нужно срочно — напиши прямо сюда в чат.",
        reply_markup=ReplyKeyboardRemove