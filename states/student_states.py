from aiogram.fsm.state import State, StatesGroup


class StudentStates(StatesGroup):
    waiting_answer = State()       # data: assignment_id
    waiting_boss_answer = State()  # data: fight_id
