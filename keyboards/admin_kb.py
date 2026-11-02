from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_main_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 Провёл занятие", callback_data="admin:lesson_start")
    kb.button(text="📚 Темы", callback_data="admin:topics")
    kb.button(text="➕ Задание в тему", callback_data="admin:add_task")
    kb.button(text="📥 Импорт заданий", callback_data="admin:import_file")
    kb.button(text="👨‍🎓 Ученики", callback_data="admin:students")
    kb.button(text="📊 Проверка ответов", callback_data="admin:errors_all")
    kb.button(text="📤 Выдать задания сейчас", callback_data="admin:push_daily")
    kb.button(text="❌ Закрыть", callback_data="admin:close")
    kb.adjust(1, 2, 2, 2, 1)
    return kb.as_markup()


def students_list_kb(students: list[tuple[int, str, int, int]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for sid, username, level, xp in students:
        label = f"@{username}" if username else f"id:{sid}"
        kb.button(text=f"👤 {label} — Lv.{level} ({xp} XP)", callback_data=f"admin:student:{sid}")
    kb.button(text="⬅️ Назад", callback_data="admin:back")
    kb.adjust(1)
    return kb.as_markup()


def students_for_lesson_kb(students: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for sid, username in students:
        label = f"@{username}" if username else f"id:{sid}"
        kb.button(text=f"👤 {label}", callback_data=f"admin:lesson_pick:{sid}")
    kb.button(text="⬅️ Назад", callback_data="admin:back")
    kb.adjust(1)
    return kb.as_markup()


def lesson_topic_matches_kb(matches: list[tuple[int, str, float]], student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tid, title, score in matches:
        kb.button(
            text=f"{title}  ({int(score * 100)}%)",
            callback_data=f"admin:lesson_topic_pick:{tid}",
        )
    kb.button(
        text="➕ Создать новую тему",
        callback_data=f"admin:lesson_topic_new:{student_id}",
    )
    kb.button(text="⬅️ Отмена", callback_data="admin:back")
    kb.adjust(1)
    return kb.as_markup()


def difficulty_kb(student_id: int, topic_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🟢 Легко", callback_data=f"admin:lesson_diff:{student_id}:{topic_id}:easy")
    kb.button(text="🟡 Средне", callback_data=f"admin:lesson_diff:{student_id}:{topic_id}:medium")
    kb.button(text="🔴 Сложно", callback_data=f"admin:lesson_diff:{student_id}:{topic_id}:hard")
    kb.adjust(3)
    return kb.as_markup()


def student_detail_kb(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 Провёл занятие с ним", callback_data=f"admin:lesson_pick:{student_id}")
    kb.button(text="📤 Выдать задания ученику", callback_data=f"admin:push_student:{student_id}")
    kb.button(text="❌ Ошибки ученика", callback_data=f"admin:errors_student:{student_id}")
    kb.button(text="✏️ Индивидуальное задание", callback_data=f"admin:ind_task:{student_id}")
    kb.button(text="⬅️ К ученикам", callback_data="admin:students")
    kb.adjust(1)
    return kb.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В админ-панель", callback_data="admin:back")
    return kb.as_markup()


TOPICS_PER_PAGE = 20


def topics_list_kb(
    topics: list[tuple[int, str, bool]],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Постраничный список тем. По TOPICS_PER_PAGE на странице."""
    total = len(topics)
    pages = max(1, (total + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE)
    page = max(0, min(page, pages - 1))

    start = page * TOPICS_PER_PAGE
    end = start + TOPICS_PER_PAGE
    chunk = topics[start:end]

    kb = InlineKeyboardBuilder()
    for tid, title, active in chunk:
        # Telegram ограничивает длину текста кнопки — обрезаем
        label = ("✅ " if active else "") + title
        if len(label) > 60:
            label = label[:57] + "…"
        kb.button(text=label, callback_data=f"admin:topic:{tid}")

    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:topics_page:{page-1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="admin:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:topics_page:{page+1}"))

    kb.button(text="➕ Новая тема", callback_data="admin:new_topic")
    kb.button(text="⬅️ В админ-панель", callback_data="admin:back")

    # adjust: по одной теме в строке, затем ряд навигации, затем по одной нижние кнопки
    layout = [1] * len(chunk)
    if nav:
        layout.append(len(nav))
    layout.extend([1, 1])
    kb.adjust(*layout)

    return kb.as_markup()


def topic_detail_kb(topic_id: int, is_active: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not is_active:
        kb.button(text="🟢 Сделать активной", callback_data=f"admin:activate_topic:{topic_id}")
    kb.button(text="📝 Задания темы", callback_data=f"admin:topic_tasks:{topic_id}")
    kb.button(text="➕ Добавить задание", callback_data=f"admin:add_task_to:{topic_id}")
    kb.button(text="⬅️ Назад", callback_data="admin:topics")
    kb.adjust(1)
    return kb.as_markup()


def topics_for_student_kb(topics: list[tuple[int, str, bool]], student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tid, title, current in topics:
        kb.button(text=("✅ " if current else "") + title, callback_data=f"admin:assign_topic:{student_id}:{tid}")
    kb.button(text="⬅️ Назад", callback_data=f"admin:student:{student_id}")
    kb.adjust(1)
    return kb.as_markup()



def error_detail_kb(assignment_id: int, student_id: int, already_accepted: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not already_accepted:
        kb.button(
            text="✅ Засчитать как правильный",
            callback_data=f"admin:accept_answer:{assignment_id}",
        )
    kb.button(
        text="🎁 Начислить XP бонусом",
        callback_data=f"admin:bonus_xp:{assignment_id}",
    )
    kb.button(
        text="⬅️ К ошибкам",
        callback_data=f"admin:errors_student:{student_id}",
    )
    kb.button(text="⬅️ В админ-панель", callback_data="admin:back")
    kb.adjust(1)
    return kb.as_markup()


def errors_list_kb(errors: list[tuple[int, str]], student_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for eid, label in errors:
        kb.button(text=label, callback_data=f"admin:err_detail:{eid}")
    if student_id:
        kb.button(text="⬅️ К ученику", callback_data=f"admin:student:{student_id}")
    else:
        kb.button(text="⬅️ Назад", callback_data="admin:back")
    kb.adjust(1)
    return kb.as_markup()
