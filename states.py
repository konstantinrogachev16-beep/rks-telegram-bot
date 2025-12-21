from aiogram.fsm.state import StatesGroup, State

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