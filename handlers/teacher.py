from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update, func
from config import ADMIN_IDS
from database.base import async_session
from database.models import User, Topic, Task, CompletedTask
from states.teacher_states import TeacherStates
from services.gamification import LEVEL_UP_XP
from services.rpg import skill_for_topic

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.message(Command("start_lesson"))
async def start_lesson(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только учителю.")
        return
    await state.set_state(TeacherStates.waiting_topic_title)
    await message.answer(
        "Введи название новой темы занятия.\n"
        "После этого она станет активной, и задания будут браться из неё."
    )


@router.message(TeacherStates.waiting_topic_title)
async def process_topic_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("Название не может быть пустым. Попробуй ещё раз.")
        return

    async with async_session() as session:
        max_order = (await session.execute(
            select(func.max(Topic.order_index))
        )).scalar() or 0
        await session.execute(update(Topic).values(is_active=False))
        topic = Topic(title=title, is_active=True, order_index=max_order + 1)
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
        topic_id = topic.id

    async with async_session() as session:
        # Назначаем новую тему только ученикам без темы.
        # Остальным — вручную через /admin.
        await session.execute(
            update(User)
            .where(User.role == "student", User.current_topic_id.is_(None))
            .values(current_topic_id=topic_id)
        )
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Тема «{title}» создана и стала активной.\n"
        f"Теперь добавляй задания командой /add_task."
    )


@router.message(Command("add_task"))
async def add_task_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только учителю.")
        return

    async with async_session() as session:
        result = await session.execute(select(Topic).where(Topic.is_active.is_(True)))
        active_topic = result.scalar_one_or_none()
        if not active_topic:
            await message.answer(
                "Нет активной темы. Сначала создай её командой /start_lesson."
            )
            return

    await state.set_state(TeacherStates.waiting_task_text)
    await message.answer(
        f"Активная тема: «{active_topic.title}».\n"
        f"Введи текст задания, которое нужно добавить:"
    )


@router.message(TeacherStates.waiting_task_text)
async def process_task_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("Текст задания не может быть пустым.")
        return

    async with async_session() as session:
        result = await session.execute(select(Topic).where(Topic.is_active.is_(True)))
        active_topic = result.scalar_one_or_none()
        if not active_topic:
            await message.answer("Активная тема исчезла. Создай новую через /start_lesson.")
            await state.clear()
            return

        task = Task(
            topic_id=active_topic.id, text=text,
            skill=skill_for_topic(active_topic.title),
        )
        session.add(task)
        await session.commit()

    await state.clear()
    await message.answer(f"✅ Задание добавлено в тему «{active_topic.title}».")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только учителю.")
        return

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.role == "student").limit(1)
        )
        student = result.scalar_one_or_none()
        if not student:
            await message.answer("Учеников пока нет.")
            return

        completed = (
            await session.execute(
                select(func.count()).select_from(CompletedTask).where(
                    CompletedTask.user_id == student.id
                )
            )
        ).scalar() or 0

        xp_in_level = student.xp % LEVEL_UP_XP
        progress = int((xp_in_level / LEVEL_UP_XP) * 10)
        bar = "█" * progress + "░" * (10 - progress)

        text = (
            f"📊 <b>Статистика ученика</b>\n"
            f"ID: <code>{student.id}</code>\n"
            f"Username: @{student.username or '—'}\n\n"
            f"⭐ Уровень: <b>{student.level}</b>\n"
            f"💠 XP: <b>{student.xp}</b>\n"
            f"📈 <code>{bar}</code> {int((xp_in_level / LEVEL_UP_XP) * 100)}%\n"
            f"🔥 Стрик: <b>{student.streak}</b> дн.\n"
            f"✅ Выполнено заданий: <b>{completed}</b>"
        )
        await message.answer(text, parse_mode="HTML")
