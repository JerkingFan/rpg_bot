from aiogram.fsm.state import State, StatesGroup


class TeacherStates(StatesGroup):
    waiting_topic_title = State()
    waiting_task_text = State()
