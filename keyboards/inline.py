from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_task_keyboard(assignment_id: int, has_answer: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Ответить", callback_data=f"answer:{assignment_id}")
    kb.button(text="💡 Подсказка", callback_data=f"hint:{assignment_id}")
    kb.button(text="⏭ Пропустить", callback_data=f"skip:{assignment_id}")
    kb.adjust(2, 1)
    return kb.as_markup()


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧙 Герой", callback_data="menu:hero")
    kb.button(text="🎯 Квест дня", callback_data="menu:quest")
    kb.button(text="🗺 Карта", callback_data="menu:map")
    kb.button(text="🏰 Босс", callback_data="menu:boss")
    kb.button(text="🛒 Магазин", callback_data="menu:shop")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    return kb.as_markup()


def quest_kb(has_assignments: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not has_assignments:
        kb.button(text="📥 Получить задания", callback_data="quest:push")
    kb.button(text="🧙 Герой", callback_data="menu:hero")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def map_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏰 Босс темы", callback_data="menu:boss")
    kb.button(text="🛒 Магазин", callback_data="menu:shop")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(2, 1)
    return kb.as_markup()


def shop_kb(items: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """items: (code, label, price)."""
    kb = InlineKeyboardBuilder()
    for code, label, price in items:
        kb.button(text=f"{label} — {price} 🪙", callback_data=f"buy:{code}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def boss_kb(active: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if active:
        kb.button(text="⚔️ Продолжить бой", callback_data="boss:continue")
    else:
        kb.button(text="⚔️ Начать бой", callback_data="boss:start")
    kb.button(text="🗺 Карта", callback_data="menu:map")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()
