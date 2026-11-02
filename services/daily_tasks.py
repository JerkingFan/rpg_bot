import html

from sqlalchemy import select, func
from aiogram import Bot
from aiogram.types import FSInputFile

from database.base import async_session
from database.models import User, Task, DailyAssignment
from services.latex_renderer import render_latex
from utils.time import local_today

TASKS_PER_DAY = 3
DIFF_ORDER = ["easy", "medium", "hard"]


def _difficulty_chain(user_diff: str) -> list[str]:
    """Порядок, в котором брать задания: сначала текущая сложность, затем остальные."""
    if user_diff not in DIFF_ORDER:
        return DIFF_ORDER
    idx = DIFF_ORDER.index(user_diff)
    return DIFF_ORDER[idx:] + DIFF_ORDER[:idx]


async def assign_daily_tasks(bot: Bot, user_id: int) -> int:
    today = local_today()

    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            return 0

        seen_result = await session.execute(
            select(DailyAssignment.task_id).where(DailyAssignment.user_id == user_id)
        )
        seen_ids = {row[0] for row in seen_result.all()}

        today_result = await session.execute(
            select(DailyAssignment.task_id).where(
                DailyAssignment.user_id == user_id,
                DailyAssignment.assigned_date == today,
            )
        )
        today_ids = {row[0] for row in today_result.all()}

        debt_result = await session.execute(
            select(DailyAssignment)
            .where(
                DailyAssignment.user_id == user_id,
                DailyAssignment.is_completed.is_(False),
                DailyAssignment.assigned_date < today,
            )
            .order_by(DailyAssignment.assigned_date)
            .limit(TASKS_PER_DAY)
        )
        debt_assignments = list(debt_result.scalars().all())

        remaining = TASKS_PER_DAY - len(debt_assignments)
        new_tasks: list[Task] = []

        # 1) новые задачи по «правильной» сложности, случайно
        if remaining > 0 and user.current_topic_id:
            for diff in _difficulty_chain(user.current_difficulty):
                if remaining <= 0:
                    break
                q = select(Task).where(
                    Task.topic_id == user.current_topic_id,
                    Task.target_user_id.is_(None),
                    Task.difficulty == diff,
                )
                if seen_ids:
                    q = q.where(Task.id.notin_(seen_ids))
                q = q.order_by(func.random()).limit(remaining)
                batch = list((await session.execute(q)).scalars().all())
                new_tasks.extend(batch)
                remaining -= len(batch)

        # 2) fallback: если в теме закончились новые — берём случайные повторы
        if remaining > 0 and user.current_topic_id:
            in_batch_ids = {t.id for t in new_tasks}
            fb_q = select(Task).where(
                Task.topic_id == user.current_topic_id,
                Task.target_user_id.is_(None),
            )
            if in_batch_ids:
                fb_q = fb_q.where(Task.id.notin_(in_batch_ids))
            if today_ids:
                fb_q = fb_q.where(Task.id.notin_(today_ids))
            fb_q = fb_q.order_by(func.random()).limit(remaining)
            fallback = list((await session.execute(fb_q)).scalars().all())
            new_tasks.extend(fallback)
            remaining -= len(fallback)

        # 3) индивидуальные (случайно)
        if remaining > 0:
            q = select(Task).where(Task.target_user_id == user_id)
            if seen_ids:
                q = q.where(Task.id.notin_(seen_ids))
            if today_ids:
                q = q.where(Task.id.notin_(today_ids))
            q = q.order_by(func.random()).limit(remaining)
            new_tasks.extend(list((await session.execute(q)).scalars().all()))

        for t in new_tasks:
            session.add(DailyAssignment(
                user_id=user_id, task_id=t.id, assigned_date=today,
            ))
        await session.commit()

        new_assignments = []
        if new_tasks:
            r = await session.execute(
                select(DailyAssignment).where(
                    DailyAssignment.user_id == user_id,
                    DailyAssignment.task_id.in_([t.id for t in new_tasks]),
                    DailyAssignment.assigned_date == today,
                    DailyAssignment.is_completed.is_(False),
                )
            )
            new_assignments = list(r.scalars().all())

        all_assignments = debt_assignments + new_assignments
        if not all_assignments:
            # Не спамим, если задания на сегодня уже выданы.
            if not today_ids:
                await bot.send_message(user_id, "📭 На сегодня заданий нет. Отдыхай!")
            return 0

        if debt_assignments:
            await bot.send_message(
                user_id,
                f"⚠️ Сегодня также нужно закрыть {len(debt_assignments)} заданий из долга.",
            )

        for i, da in enumerate(all_assignments, 1):
            task = await session.get(Task, da.task_id)
            if task:
                await _send_task(bot, user_id, task, i, da.id)

        return len(all_assignments)


async def _send_task(bot: Bot, user_id: int, task: Task, index: int, assignment_id: int) -> None:
    from keyboards.inline import get_task_keyboard

    caption_lines = []
    if task.target_user_id:
        caption_lines.append("🎯 <b>Индивидуальное</b>")
    caption_lines.append(f"📝 <b>Задание {index}</b>")
    caption_lines.append("<i>Отправь ответ текстом или попроси подсказку.</i>")
    caption = "\n".join(caption_lines)

    kb = get_task_keyboard(assignment_id, bool(task.answer))

    try:
        photo_path = render_latex(task.text)
        await bot.send_photo(
            user_id, FSInputFile(photo_path),
            caption=caption, reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        tag = "🎯 <b>Индивидуальное</b>\n" if task.target_user_id else ""
        await bot.send_message(
            user_id,
            f"{tag}{caption}\n\n{html.escape(task.text)}",
            reply_markup=kb,
            parse_mode="HTML",
        )
