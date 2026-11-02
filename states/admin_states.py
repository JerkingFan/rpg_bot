from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    waiting_new_topic_title = State()
    waiting_task_for_topic = State()
    waiting_individual_task = State()
    waiting_import_file = State()

    # Lesson flow
    waiting_lesson_topic_text = State()   # data: student_id, student_name
    waiting_new_lesson_topic = State()    # data: student_id, student_name, query
