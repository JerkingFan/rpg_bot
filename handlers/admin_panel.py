import html

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update, func, desc, or_

from config import ADMIN_IDS
from database.base import async_session
from database.models import (
    User, Topic, Task, DailyAssignment, CompletedTask,
    Achievement, UserAchievement, LessonSession,
)
from states.admin_states import AdminStates
from keyboards.admin_kb import (
    admin_main_kb, students_list_kb, students_for_lesson_kb,
    lesson_topic_matches_kb, difficulty_kb, student_detail_kb,
    topics_list_kb, topic_detail_kb, topics_for_student_kb,
    topic_tasks_kb, TASKS_PER_PAGE,
    back_to_admin_kb, errors_list_kb, error_detail_kb)
from services.gamification import LEVEL_UP_XP
from services.similarity import find_similar_topics
from services.daily_tasks import assign_daily_tasks
from services.rpg import (
    COINS_BY_DIFFICULTY, XP_BY_DIFFICULTY, award_bonus, complete_task,
    skill_for_topic,
)

router = Router()


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def _students():
    async with async_session() as session:
        q = select(User).where(User.role == "student").order_by(User.xp.desc())
        if ADMIN_IDS:
            q = q.where(User.id.notin_(ADMIN_IDS))
        return list((await session.execute(q)).scalars().all())


# ---------------- Вход ----------------
@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для учителя.")
        return
    await state.clear()
    await message.answer("🎛 <b>Админ-панель</b>", reply_markup=admin_main_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin:back")
async def cb_back(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("🎛 <b>Админ-панель</b>", reply_markup=admin_main_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:close")
async def cb_close(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.delete()
    await callback.answer("Закрыто")


# ================== 📖 ПРОВЁЛ ЗАНЯТИЕ ==================
@router.callback_query(F.data == "admin:lesson_start")
async def cb_lesson_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    students = await _students()
    if not students:
        await callback.answer("Нет учеников.", show_alert=True)
        return
    rows = [(s.id, s.username or "") for s in students]
    await callback.message.edit_text(
        "📖 <b>Провёл занятие</b>\nС кем?", reply_markup=students_for_lesson_kb(rows), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:lesson_pick:"))
async def cb_lesson_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    try:
        sid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка. Открой /admin заново.", show_alert=True)
        return
    async with async_session() as session:
        student = await session.get(User, sid)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_lesson_topic_text)
    await state.update_data(student_id=sid, student_name=student.username or str(sid))
    await callback.message.edit_text(
        f"📖 Ученик: <b>@{html.escape(str(student.username or sid))}</b>\n\n"
        f"Введи тему занятия (свободным текстом):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.waiting_lesson_topic_text)
async def process_lesson_topic_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пусто. Введи тему ещё раз:")
        return

    data = await state.get_data()
    sid = data.get("student_id")
    if not sid:
        await message.answer(
            "⚠️ Потерян контекст (бот перезапускался?).\n"
            "Начни заново: /admin → 📖 Провёл занятие",
            reply_markup=admin_main_kb(),
        )
        await state.clear()
        return

    async with async_session() as session:
        topics = list((await session.execute(
            select(Topic).order_by(Topic.title)
        )).scalars().all())

    topic_rows = [(t.id, t.title) for t in topics]
    matches = find_similar_topics(query, topic_rows, top_n=3)
    matches = [m for m in matches if m[2] > 0.15]

    await state.update_data(query=query)

    if not matches:
        await message.answer(
            f"Похожих тем не нашёл для «{html.escape(query)}».\n"
            f"<i>Придётся создать новую.</i>",
            parse_mode="HTML",
        )
        await state.set_state(AdminStates.waiting_new_lesson_topic)
        await message.answer("✏️ Введи точное название новой темы:")
        return

    text = (
        f"🔎 По запросу «<b>{html.escape(query)}</b>» найдено:\n\n"
        "Выбери тему из списка или создай новую:"
    )
    await message.answer(
        text,
        reply_markup=lesson_topic_matches_kb(matches, int(sid)),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:lesson_topic_pick:"))
async def cb_lesson_topic_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    try:
        tid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка. Открой /admin заново.", show_alert=True)
        return
    data = await state.get_data()
    sid = data.get("student_id")
    if not sid:
        await callback.answer(
            "Контекст потерян. Начни заново: /admin → 📖 Провёл занятие",
            show_alert=True,
        )
        await state.clear()
        return
    await callback.message.edit_text(
        "🎚 Выбери <b>сложность</b> заданий на каждый день:",
        reply_markup=difficulty_kb(int(sid), tid),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:lesson_topic_new"))
async def cb_lesson_topic_new(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    # новый формат: admin:lesson_topic_new:<sid>
    # старый формат: admin:lesson_topic_new (из старых сообщений)
    if len(parts) >= 3:
        try:
            sid = int(parts[2])
            await state.update_data(student_id=sid)
        except ValueError:
            pass

    data = await state.get_data()
    if not data.get("student_id"):
        await callback.answer(
            "Контекст потерян (бот перезапускался?). Начни заново: /admin → 📖 Провёл занятие",
            show_alert=True,
        )
        await state.clear()
        return

    await state.set_state(AdminStates.waiting_new_lesson_topic)
    await callback.message.edit_text("✏️ Введи точное название новой темы:")
    await callback.answer()


@router.message(AdminStates.waiting_new_lesson_topic)
async def process_new_lesson_topic(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Пусто. Введи название:")
        return
    async with async_session() as session:
        topic = Topic(title=title, is_active=False)
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
    data = await state.get_data()
    sid = data.get("student_id")
    await state.clear()
    if not sid:
        await message.answer(
            f"✅ Тема «{html.escape(title)}» создана.\n\n"
            "Но контекст ученика потерялся. Начни заново: /admin → 📖 Провёл занятие",
            reply_markup=admin_main_kb(),
        )
        return
    await message.answer(
        f"✅ Тема «{html.escape(title)}» создана.\n🎚 Выбери сложность:",
        reply_markup=difficulty_kb(int(sid), topic.id),
    )


@router.callback_query(F.data.startswith("admin:lesson_diff:"))
async def cb_lesson_diff(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Некорректная кнопка. Открой /admin заново.", show_alert=True)
        return
    _, _, sid_s, tid_s, diff = parts
    try:
        sid, tid = int(sid_s), int(tid_s)
    except ValueError:
        await callback.answer(
            "Ошибка кнопки (потерян контекст). Начни заново: /admin → 📖 Провёл занятие",
            show_alert=True,
        )
        await state.clear()
        return
    async with async_session() as session:
        student = await session.get(User, sid)
        topic = await session.get(Topic, tid)
        if not student or not topic:
            await callback.answer("Не найдено", show_alert=True)
            return
        student.current_topic_id = tid
        student.current_difficulty = diff
        session.add(LessonSession(
            user_id=sid, topic_id=tid, difficulty=diff,
        ))
        await session.commit()

    diff_ru = {"easy": "🟢 Легко", "medium": "🟡 Средне", "hard": "🔴 Сложно"}.get(diff, diff)
    await callback.message.edit_text(
        f"✅ <b>Занятие зафиксировано</b>\n\n"
        f"👤 Ученик: <b>@{html.escape(str(student.username or sid))}</b>\n"
        f"📚 Тема: <b>{html.escape(topic.title)}</b>\n"
        f"🎚 Сложность: <b>{diff_ru}</b>\n\n"
        f"Ученик будет каждый день получать задания по этой теме.",
        reply_markup=back_to_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer("Сохранено")


# ================== 📊 ОШИБКИ ==================
@router.callback_query(F.data == "admin:errors_all")
async def cb_errors_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with async_session() as session:
        rows = (await session.execute(
            select(DailyAssignment, User, Task)
            .join(User, User.id == DailyAssignment.user_id)
            .join(Task, Task.id == DailyAssignment.task_id)
            .where(
                DailyAssignment.is_completed.is_(True),
                or_(
                    DailyAssignment.is_correct.is_(False),
                    DailyAssignment.is_correct.is_(None),
                ),
            )
            .order_by(desc(DailyAssignment.answered_at))
            .limit(20)
        )).all()
    if not rows:
        await callback.message.edit_text(
            "🎉 Ошибок пока нет.", reply_markup=back_to_admin_kb(),
        )
        await callback.answer(); return

    btns = []
    for a, u, t in rows:
        who = f"@{u.username}" if u.username else str(u.id)
        mark = "📨" if a.is_correct is None else "❌"
        label = f"{mark} {who}: {t.text[:40]}{'…' if len(t.text) > 40 else ''}"
        btns.append((a.id, label))

    await callback.message.edit_text(
        "📊 <b>Ошибки учеников</b> (последние 20)",
        reply_markup=errors_list_kb(btns),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:errors_student:"))
async def cb_errors_student(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    sid = int(callback.data.split(":")[2])
    async with async_session() as session:
        rows = (await session.execute(
            select(DailyAssignment, Task)
            .join(Task, Task.id == DailyAssignment.task_id)
            .where(
                DailyAssignment.user_id == sid,
                DailyAssignment.is_completed.is_(True),
                or_(
                    DailyAssignment.is_correct.is_(False),
                    DailyAssignment.is_correct.is_(None),
                ),
            )
            .order_by(desc(DailyAssignment.answered_at))
            .limit(20)
        )).all()
    if not rows:
        await callback.message.edit_text(
            "🎉 У этого ученика ошибок нет.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer(); return

    btns = [
        (a.id, f"{'📨' if a.is_correct is None else '❌'} {t.text[:40]}")
        for a, t in rows
    ]
    await callback.message.edit_text(
        "❌ <b>Ошибки ученика</b>",
        reply_markup=errors_list_kb(btns, student_id=sid),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:err_detail:"))
async def cb_err_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    aid = int(callback.data.split(":")[2])
    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a:
            await callback.answer("Не найдено", show_alert=True); return
        task = await session.get(Task, a.task_id)
        student = await session.get(User, a.user_id)
    who = f"@{student.username}" if student.username else student.id
    title = "📨 <b>Ответ на проверку</b>" if a.is_correct is None else "❌ <b>Ошибка</b>"
    text = (
        f"{title}\n\n"
        f"👤 Ученик: <b>{html.escape(str(who))}</b>\n"
        f"📝 Задание: <code>{html.escape(task.text)}</code>\n\n"
        f"💬 Ответ ученика: <code>{html.escape(a.student_answer or '—')}</code>\n"
        f"✅ Правильный: <code>{html.escape(task.answer or '—')}</code>\n"
        f"🕒 {a.answered_at.strftime('%Y-%m-%d %H:%M') if a.answered_at else '—'}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=error_detail_kb(a.id, student.id, already_accepted=bool(a.is_correct)),
        parse_mode="HTML",
    )
    await callback.answer()


# ================== УЧЕНИКИ ==================
@router.callback_query(F.data == "admin:students")
async def cb_students(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    students = await _students()
    if not students:
        await callback.message.edit_text("👨‍🎓 Нет учеников.", reply_markup=back_to_admin_kb())
        await callback.answer(); return
    rows = [(s.id, s.username or "", s.level, s.xp) for s in students]
    await callback.message.edit_text(
        f"👨‍🎓 <b>Ученики ({len(students)})</b>",
        reply_markup=students_list_kb(rows), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:student:"))
async def cb_student_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    sid = int(callback.data.split(":")[2])
    async with async_session() as session:
        s = await session.get(User, sid)
        if not s:
            await callback.answer("Нет ученика", show_alert=True); return
        correct = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == sid, DailyAssignment.is_correct.is_(True))
        )).scalar() or 0
        wrong = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == sid, DailyAssignment.is_correct.is_(False))
        )).scalar() or 0
        topic = await session.get(Topic, s.current_topic_id) if s.current_topic_id else None

    xp_in = s.xp % LEVEL_UP_XP
    pct = int((xp_in / LEVEL_UP_XP) * 100)
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    text = (
        f"👤 <b>@{html.escape(str(s.username or s.id))}</b>\n\n"
        f"⭐ Уровень: <b>{s.level}</b>\n"
        f"💠 XP: <b>{s.xp}</b>\n"
        f"📈 <code>{bar}</code> {pct}%\n"
        f"🔥 Стрик: <b>{s.streak}</b>\n"
        f"✅ Верно: <b>{correct}</b>   ❌ Ошибок: <b>{wrong}</b>\n"
        f"📚 Тема: <b>{html.escape(topic.title) if topic else '—'}</b>\n"
        f"🎚 Сложность: <b>{s.current_difficulty}</b>"
    )
    await callback.message.edit_text(text, reply_markup=student_detail_kb(sid), parse_mode="HTML")
    await callback.answer()


# ---------------- Смена темы вручную ----------------
@router.callback_query(F.data.startswith("admin:set_topic:"))
async def cb_set_topic(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    sid = int(callback.data.split(":")[2])
    async with async_session() as session:
        student = await session.get(User, sid)
        topics = list((await session.execute(select(Topic).order_by(Topic.title))).scalars().all())
    if not topics:
        await callback.answer("Нет тем.", show_alert=True); return
    rows = [(t.id, t.title, t.id == student.current_topic_id) for t in topics]
    await callback.message.edit_text(
        "📚 Выбери тему:", reply_markup=topics_for_student_kb(rows, sid),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:assign_topic:"))
async def cb_assign_topic(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    _, _, sid_s, tid_s = callback.data.split(":")
    sid, tid = int(sid_s), int(tid_s)
    async with async_session() as session:
        await session.execute(update(User).where(User.id == sid).values(current_topic_id=tid))
        await session.commit()
    await callback.answer("Тема назначена", show_alert=False)


# ---------------- Прочие старые функции ----------------
@router.callback_query(F.data == "admin:noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "admin:topics")
async def cb_topics(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with async_session() as session:
        topics = list((await session.execute(
            select(Topic).order_by(Topic.title)
        )).scalars().all())
    rows = [(t.id, t.title, t.is_active) for t in topics]
    await callback.message.edit_text(
        f"📚 <b>Темы ({len(rows)})</b>\n"
        f"✅ — активная. Выбери для управления:",
        reply_markup=topics_list_kb(rows, page=0),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:topics_page:"))
async def cb_topics_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    page = int(callback.data.split(":")[2])
    async with async_session() as session:
        topics = list((await session.execute(
            select(Topic).order_by(Topic.title)
        )).scalars().all())
    rows = [(t.id, t.title, t.is_active) for t in topics]
    await callback.message.edit_text(
        f"📚 <b>Темы ({len(rows)})</b>",
        reply_markup=topics_list_kb(rows, page=page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:topic:"))
async def cb_topic_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    tid = int(callback.data.split(":")[2])
    async with async_session() as session:
        topic = await session.get(Topic, tid)
        n = (await session.execute(
            select(func.count()).select_from(Task).where(Task.topic_id == tid)
        )).scalar() or 0
    await callback.message.edit_text(
        f"📖 <b>{html.escape(topic.title)}</b>\nЗаданий: {n}",
        reply_markup=topic_detail_kb(topic.id, topic.is_active), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:activate_topic:"))
async def cb_activate_topic(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        tid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    async with async_session() as session:
        topic = await session.get(Topic, tid)
        if not topic:
            await callback.answer("Тема не найдена", show_alert=True)
            return
        await session.execute(update(Topic).values(is_active=False))
        await session.execute(update(Topic).where(Topic.id == tid).values(is_active=True))
        await session.commit()
    await callback.message.edit_text(
        f"✅ Тема «{html.escape(topic.title)}» теперь активна.",
        reply_markup=topic_detail_kb(topic.id, True),
        parse_mode="HTML",
    )
    await callback.answer("Активирована")


@router.callback_query(F.data.startswith("admin:push_student:"))
async def cb_push_student(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        sid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    await callback.answer("Выдаю задания…")
    sent = await assign_daily_tasks(callback.bot, sid)
    await callback.message.answer(f"📤 Ученику выдано заданий: <b>{sent}</b>", parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:topic_tasks:"))
async def cb_topic_tasks(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    try:
        tid = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    async with async_session() as session:
        topic = await session.get(Topic, tid)
        if not topic:
            await callback.answer("Тема не найдена", show_alert=True)
            return
        total = (await session.execute(
            select(func.count()).select_from(Task).where(Task.topic_id == tid)
        )).scalar() or 0
        pages = max(1, (total + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)
        page = max(0, min(page, pages - 1))
        tasks = list((await session.execute(
            select(Task)
            .where(Task.topic_id == tid)
            .order_by(Task.difficulty, Task.id)
            .offset(page * TASKS_PER_PAGE)
            .limit(TASKS_PER_PAGE)
        )).scalars().all())

    if not tasks:
        text = f"📝 В теме «{html.escape(topic.title)}» нет заданий."
    else:
        lines = [
            f"📝 <b>«{html.escape(topic.title)}»</b>",
            f"Всего заданий: <b>{total}</b> · "
            f"страница <b>{page + 1}/{pages}</b>\n",
        ]
        size = sum(len(line) for line in lines)
        for t in tasks:
            tag = "🎯" if t.target_user_id else f"[{t.difficulty}]"
            ans = f" → {html.escape(t.answer[:80])}" if t.answer else ""
            line = f"<b>#{t.id}</b> {tag} {html.escape(t.text[:200])}{ans}"
            if size + len(line) > 3800:
                lines.append("…(обрезано, открой следующую страницу)")
                break
            lines.append(line)
            size += len(line) + 1
        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=topic_tasks_kb(topic.id, page, pages),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:new_topic")
async def cb_new_topic(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_new_topic_title)
    await callback.message.edit_text("✏️ Название новой темы:")
    await callback.answer()


@router.message(AdminStates.waiting_new_topic_title)
async def process_new_topic(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Пусто, ещё раз:")
        return
    async with async_session() as session:
        session.add(Topic(title=title, is_active=False))
        await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Тема «{html.escape(title)}» создана.", reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "admin:add_task")
async def cb_add_task(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    async with async_session() as session:
        active = (await session.execute(
            select(Topic).where(Topic.is_active.is_(True))
        )).scalar_one_or_none()
    if not active:
        await callback.answer("Нет активной темы.", show_alert=True); return
    await state.set_state(AdminStates.waiting_task_for_topic)
    await state.update_data(topic_id=active.id, topic_title=active.title)
    await callback.message.edit_text(
        f"✏️ Тема: <b>{html.escape(active.title)}</b>\nВведи задание в формате:\n"
        f"<code>текст задания\n"
        f"= ответ\n"
        f"[сложность: easy|medium|hard, по умолчанию medium]</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:add_task_to:"))
async def cb_add_task_to(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    tid = int(callback.data.split(":")[2])
    async with async_session() as session:
        topic = await session.get(Topic, tid)
    await state.set_state(AdminStates.waiting_task_for_topic)
    await state.update_data(topic_id=topic.id, topic_title=topic.title)
    await callback.message.edit_text(
        f"✏️ Тема: <b>{html.escape(topic.title)}</b>\nВведи задание:",
        parse_mode="HTML",
    )
    await callback.answer()


def _parse_task_input(raw: str) -> tuple[str, str | None, str]:
    """Из «текст\\n= ответ\\n[easy]» вытащить (текст, ответ, difficulty)."""
    difficulty = "medium"
    lines = raw.split("\n")
    answer = None
    keep = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("[easy]", "[medium]", "[hard]"):
            difficulty = stripped.strip("[]").lower()
            continue
        if stripped.startswith("= "):
            answer = stripped[2:].strip()
            continue
        if stripped.startswith("="):
            answer = stripped[1:].strip()
            continue
        keep.append(line)
    text = "\n".join(keep).strip()
    return text, answer, difficulty


@router.message(AdminStates.waiting_task_for_topic)
async def process_new_task(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text, answer, diff = _parse_task_input(message.text or "")
    if not text:
        await message.answer("Текст задания пустой. Ещё раз:")
        return
    data = await state.get_data()
    tid = data.get("topic_id")
    async with async_session() as session:
        topic = await session.get(Topic, tid)
        session.add(Task(
            topic_id=tid, text=text, answer=answer, difficulty=diff,
            target_user_id=None, skill=skill_for_topic(topic.title if topic else ""),
        ))
        await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Добавлено (сложность: {diff}, ответ: {'есть' if answer else 'нет'}).",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "admin:push_daily")
async def cb_push(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    students = await _students()
    if not students:
        await callback.answer("Нет учеников", show_alert=True); return
    # Сразу гасим спиннер: рассылка может занять больше времени, чем живёт callback.
    await callback.answer("Выдаю задания…")
    await callback.message.edit_text(f"📤 Выдаю задания {len(students)} ученикам...")
    sent = 0
    for s in students:
        try:
            await assign_daily_tasks(callback.bot, s.id)
            sent += 1
        except Exception:
            pass
    await callback.message.edit_text(
        f"✅ Отправлено: {sent}/{len(students)}", reply_markup=back_to_admin_kb(),
    )


# Индивидуальное задание (старое, оставляем)
@router.callback_query(F.data.startswith("admin:ind_task:"))
async def cb_ind_task(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    sid = int(callback.data.split(":")[2])
    async with async_session() as session:
        s = await session.get(User, sid)
        if not s.current_topic_id:
            await callback.answer("Сначала назначь тему.", show_alert=True); return
        topic = await session.get(Topic, s.current_topic_id)
    await state.set_state(AdminStates.waiting_individual_task)
    await state.update_data(student_id=sid, student_name=s.username or str(sid))
    await callback.message.edit_text(
        f"✏️ Индивидуальное для <b>@{html.escape(str(s.username or sid))}</b> "
        f"(тема: {html.escape(topic.title)})\n"
        f"Формат: текст, опционально «\\n= ответ».",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.waiting_individual_task)
async def process_ind_task(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text, answer, diff = _parse_task_input(message.text or "")
    if not text:
        await message.answer("Пусто. Ещё раз:")
        return
    data = await state.get_data()
    sid = data.get("student_id")
    async with async_session() as session:
        s = await session.get(User, sid)
        topic = await session.get(Topic, s.current_topic_id) if s.current_topic_id else None
        session.add(Task(
            topic_id=s.current_topic_id, text=text, answer=answer,
            difficulty=diff, target_user_id=sid,
            skill=skill_for_topic(topic.title if topic else ""),
        ))
        await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Индивидуальное задание создано для @{html.escape(str(data.get('student_name')))}.",
        reply_markup=admin_main_kb(),
    )


# Импорт из файла (старый)
@router.callback_query(F.data == "admin:import_file")
async def cb_import_file(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_import_file)
    await callback.message.edit_text(
        "📥 <b>Импорт заданий</b>\n\n"
        "Отправь .txt файл.\n\n"
        "<b>Формат:</b>\n"
        "<pre>[topic] Математика | medium\n"
        "Найди $2x+3y$\n"
        "= 16\n"
        "---\n"
        "Реши $x^2-5x+6=0$\n"
        "= 2, 3\n\n"
        "[individual] @username | Геометрия\n"
        "$S = \\pi r^2$\n"
        "= 28.26</pre>\n\n"
        "Строка <code>= ответ</code> опциональна. "
        "Сложность: <code>easy | medium | hard</code>, по умолчанию medium.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.waiting_import_file, F.document)
async def process_import(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    doc = message.document
    if not doc.file_name.lower().endswith(".txt"):
        await message.answer("Нужен .txt")
        return
    file = await message.bot.download(doc)
    raw = file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1251")

    from services.tasks_importer import import_from_file
    stats = await import_from_file(content)
    await state.clear()

    text = (
        f"✅ Импорт готов\n"
        f"📚 Тем создано: {stats['topics_created']}\n"
        f"📝 Заданий: {stats['tasks_added']}"
    )
    if stats["errors"]:
        text += "\n⚠️ Ошибки:\n" + "\n".join(f"• {e}" for e in stats["errors"][:5])
    await message.answer(text, reply_markup=admin_main_kb(), parse_mode="HTML")


# ================== ✅ Ручное засчитывание ответа ==================
@router.callback_query(F.data.startswith("admin:accept_answer:"))
async def cb_accept_answer(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    try:
        aid = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    from keyboards.admin_kb import error_detail_kb

    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        if a.is_correct:
            await callback.answer("Этот ответ уже засчитан", show_alert=True)
            return

        task = await session.get(Task, a.task_id)
        student = await session.get(User, a.user_id)
        if not task or not student:
            await callback.answer("Задание или ученик не найдены", show_alert=True)
            return

        # Полный путь через RPG-сервис: XP, монеты, навык, мастерство, квест.
        result = await complete_task(
            session, student, task, a, source="manual", manual=True,
        )
        a.manually_accepted = True
        await session.commit()

    # уведомляем ученика
    try:
        msg = (
            f"✅ <b>Учитель засчитал твой ответ как правильный!</b>\n"
            f"Задание: «{html.escape(task.text[:100])}{'…' if len(task.text) > 100 else ''}»\n"
            f"Твой ответ: <code>{html.escape(a.student_answer or '—')}</code>\n\n"
            f"+{result['earned_xp']} XP, +{result['earned_coins']} 🪙"
        )
        if result["leveled_up"]:
            msg += f"\n🎉 Новый уровень: <b>{result['new_level']}</b>!"
        for ach in result["awarded_achievements"]:
            msg += f"\n🏆 {html.escape(ach.title)}"
        await callback.bot.send_message(student.id, msg, parse_mode="HTML")
    except Exception as e:
        await callback.answer(f"Ответ засчитан, но ученику не удалось отправить сообщение: {e}", show_alert=True)
    else:
        await callback.answer("✅ Ответ засчитан, награда начислена")

    # обновляем карточку ошибки
    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>Засчитано вручную</b>",
        reply_markup=error_detail_kb(aid, student.id, already_accepted=True),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:bonus_xp:"))
async def cb_bonus_xp(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    try:
        aid = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    from keyboards.admin_kb import error_detail_kb

    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        task = await session.get(Task, a.task_id)
        student = await session.get(User, a.user_id)
        if not task or not student:
            await callback.answer("Задание или ученик не найдены", show_alert=True)
            return

        # НЕ меняем is_correct и не создаём CompletedTask: это бонус, а не решение.
        result = await award_bonus(
            session, student,
            xp=XP_BY_DIFFICULTY.get(task.difficulty, 15),
            coins=COINS_BY_DIFFICULTY.get(task.difficulty, 8),
        )
        await session.commit()

    try:
        msg = (
            f"🎁 <b>Учитель начислил тебе бонус</b>\n"
            f"+{result['xp']} XP, +{result['coins']} 🪙 за усилие по заданию"
        )
        if result["leveled_up"]:
            msg += f"\n🎉 Новый уровень: <b>{result['new_level']}</b>!"
        await callback.bot.send_message(student.id, msg, parse_mode="HTML")
    except Exception as e:
        await callback.answer(f"XP начислены, но ученику не удалось отправить: {e}", show_alert=True)
    else:
        await callback.answer("🎁 Бонусный XP начислен")

    await callback.message.edit_text(
        callback.message.html_text + "\n\n🎁 <b>Бонусный XP начислен</b>",
        reply_markup=error_detail_kb(aid, student.id, already_accepted=a.is_correct or False),
        parse_mode="HTML",
    )
