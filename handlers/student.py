import html

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, FSInputFile
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from config import ADMIN_IDS
from database.base import async_session
from database.models import User, DailyAssignment, Task, BossFight, Item, Inventory
from services.answer_checker import check_answer
from services.daily_tasks import assign_daily_tasks
from services.latex_renderer import render_latex
from services.rpg import (
    SKILLS,
    answer_boss, boss_current_task, buy_item, complete_task, ensure_daily_reset,
    get_daily_quest, hero_card, map_data, map_text, register_wrong_attempt,
    start_boss, submit_for_review, use_hint,
)
from states.student_states import StudentStates
from utils.time import local_today
from keyboards.inline import (
    back_to_menu_kb, boss_kb, main_menu_kb, map_kb,
    quest_kb, shop_kb,
)

router = Router()


# ─────────────────────────── Вспомогательное ───────────────────────────

async def _get_user(session, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def _notify_admins(bot, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


def _task_feedback_edit(message: Message, text: str) -> None:
    if not message.reply_to_message:
        return
    try:
        if message.reply_to_message.photo:
            message.reply_to_message.edit_caption(caption=text, parse_mode="HTML")
        else:
            message.reply_to_message.edit_text(text, parse_mode="HTML")
    except Exception:
        pass


def _result_lines(result: dict, assignment: DailyAssignment) -> list[str]:
    lines = ["✅ <b>Верно!</b>"]
    if result["first_clear"]:
        lines.append(f"+{result['earned_xp']} XP, +{result['earned_coins']} 🪙")
        if result["earned_xp"] == 0:
            lines.append("Дневной лимит XP уже достигнут — приходи завтра.")
    else:
        lines.append("Это повтор — XP не начисляется, но практика засчитана.")
    if assignment.used_hint:
        lines.append("💡 Подсказка немного снизила награду.")

    lines.append(
        f"🧠 {SKILLS.get(result['skill'], result['skill'])}: "
        f"уровень {result['skill_level']}"
    )
    lines.append(f"🗺 Мастерство темы: {result['mastery']}%")

    if result["leveled_up"]:
        lines.append(f"🎉 <b>Новый уровень: {result['new_level']}!</b>")
    for ach in result["awarded_achievements"]:
        lines.append(f"🏆 {html.escape(ach.title)}")

    qr = result.get("quest_reward")
    if qr:
        lines.append(
            f"🎯 Квест «{html.escape(qr['title'])}» выполнен: "
            f"+{qr['xp']} XP, +{qr['coins']} 🪙"
        )
    return lines


# ─────────────────────────── Меню ───────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🎛 <b>Главное меню</b>\n\nВыбери, куда идём:",
        reply_markup=main_menu_kb(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu:hero")
async def cb_menu_hero(callback: CallbackQuery):
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        text = await hero_card(session, user)
        await session.commit()
    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "menu:quest")
async def cb_menu_quest(callback: CallbackQuery):
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        ensure_daily_reset(user)
        quest, uq = await get_daily_quest(session, user)
        today = local_today()
        count = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == user.id,
                DailyAssignment.assigned_date == today,
            )
        )).scalar() or 0
        await session.commit()

    text = (
        f"🎯 <b>{html.escape(quest.title)}</b>\n\n"
        f"{html.escape(quest.description)}\n\n"
        f"Прогресс: <b>{uq.progress}/{quest.goal_value}</b>\n"
        f"Награда: +{quest.reward_xp} XP, +{quest.reward_coins} 🪙\n"
    )
    if count:
        text += f"\n📝 Заданий на сегодня: <b>{count}</b>"
    else:
        text += "\nЗаданий пока нет — забери квест."
    await callback.message.edit_text(
        text, reply_markup=quest_kb(has_assignments=count > 0), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "quest:push")
async def cb_quest_push(callback: CallbackQuery):
    await callback.answer("Выдаю задания…")
    sent = await assign_daily_tasks(callback.bot, callback.from_user.id)
    if sent == 0:
        await callback.message.answer("📭 Новых заданий нет. Возможно, они уже выданы.")


@router.callback_query(F.data == "menu:map")
async def cb_menu_map(callback: CallbackQuery):
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        rows = await map_data(session, user)
        text = map_text(rows)
        await session.commit()
    await callback.message.edit_text(text, reply_markup=map_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "menu:shop")
async def cb_menu_shop(callback: CallbackQuery):
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        text, kb = await _shop_view(session, user)
        await session.commit()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


async def _shop_view(session, user: User) -> tuple[str, object]:
    items = list((await session.execute(
        select(Item).where(Item.is_active.is_(True)).order_by(Item.price)
    )).scalars().all())
    inv_rows = (await session.execute(
        select(Item.code, Inventory.quantity)
        .join(Inventory, Inventory.item_id == Item.id)
        .where(Inventory.user_id == user.id)
    )).all()
    inv = {code: qty for code, qty in inv_rows}

    lines = [
        "🛒 <b>Магазин</b>",
        f"🪙 У тебя: <b>{user.coins}</b> монет\n",
    ]
    for item in items:
        have = f" (у тебя: {inv[item.code]})" if inv.get(item.code) else ""
        lines.append(
            f"{item.emoji} <b>{html.escape(item.title)}</b> — {item.price} 🪙{have}\n"
            f"   <i>{html.escape(item.description)}</i>"
        )
    kb = shop_kb([(i.code, f"{i.emoji} {i.title}", i.price) for i in items])
    return "\n".join(lines), kb


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        result = await buy_item(session, user, code)
        await session.commit()
        if not result["ok"]:
            await callback.answer(result["error"], show_alert=True)
            return
        item = result["item"]
        text, kb = await _shop_view(session, user)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(f"Куплено: {item.title}")


# ─────────────────────────── Ответы на задания ───────────────────────────

@router.callback_query(F.data.startswith("answer:"))
async def cb_answer(callback: CallbackQuery, state: FSMContext):
    try:
        aid = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a or a.is_completed or a.user_id != callback.from_user.id:
            await callback.answer("Задание уже закрыто.", show_alert=True)
            return
    await state.set_state(StudentStates.waiting_answer)
    await state.update_data(assignment_id=aid)
    await callback.message.reply("✏️ Напиши свой ответ:")
    await callback.answer()


@router.callback_query(F.data.startswith("hint:"))
async def cb_hint(callback: CallbackQuery):
    try:
        aid = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a or a.is_completed or a.user_id != callback.from_user.id:
            await callback.answer("Задание уже закрыто.", show_alert=True)
            return
        task = await session.get(Task, a.task_id)
        user = await session.get(User, callback.from_user.id)
        if not task or not user:
            await callback.answer("Задание не найдено.", show_alert=True)
            return
        result = await use_hint(session, user, a, task)
        await session.commit()

    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return

    await callback.message.reply(
        f"💡 <b>Подсказка</b>\n\n{html.escape(result['hint'])}",
        parse_mode="HTML",
    )
    await callback.answer("Подсказка получена")


@router.message(StudentStates.waiting_answer)
async def process_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    aid = data.get("assignment_id")
    student_raw = (message.text or "").strip()
    await state.clear()

    if not aid or not student_raw:
        await message.answer("Ответ пустой. Нажми «Ответить» заново.")
        return

    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a or a.is_completed or a.user_id != message.from_user.id:
            await message.answer("Это задание уже закрыто.")
            return
        task = await session.get(Task, a.task_id)
        user = await session.get(User, message.from_user.id)
        if not task or not user:
            await message.answer("Задание не найдено.")
            return

        # Задача без эталона: не даём XP, отправляем учителю.
        if not (task.answer or "").strip():
            await submit_for_review(session, user, a, task, student_raw)
            await session.commit()
            await _notify_admins(
                message.bot,
                "📨 <b>Ответ на ручную проверку</b>\n"
                f"👤 @{html.escape(user.username or str(user.id))}\n"
                f"📝 {html.escape(task.text[:200])}\n"
                f"💬 {html.escape(student_raw[:300])}\n\n"
                "Засчитай вручную в /admin → 📊 Проверка ответов.",
            )
            await message.answer(
                "📨 Ответ отправлен учителю на проверку.\n"
                "XP начислим после проверки."
            )
            return

        is_correct, reason = check_answer(student_raw, task.answer or "")

        if is_correct:
            result = await complete_task(session, user, task, a)
            await session.commit()
            feedback = "\n".join(_result_lines(result, a))
            _task_feedback_edit(message, feedback)
            await message.answer(feedback, parse_mode="HTML")
            return

        result = await register_wrong_attempt(session, user, a, task, student_raw)
        await session.commit()

        if not result["final"]:
            await message.answer(
                "❌ Пока неверно.\n"
                f"Осталась ещё {result['remaining']} попытка. Попробуй другой ход."
            )
            return

        feedback = (
            "❌ <b>Неверно.</b>\n"
            f"Правильный ответ: <code>{html.escape(task.answer or '—')}</code>\n"
            "XP не начисляется, но ошибка сохранена — учитель её увидит."
        )
        _task_feedback_edit(message, feedback)
        await message.answer(feedback, parse_mode="HTML")
        await _notify_admins(
            message.bot,
            "❌ <b>Ошибка ученика</b>\n"
            f"👤 @{html.escape(user.username or str(user.id))}\n"
            f"📝 {html.escape(task.text[:200])}\n"
            f"💬 {html.escape(student_raw[:300])}\n"
            f"✅ Правильный: <code>{html.escape(task.answer or '—')}</code>\n\n"
            "Можно засчитать вручную в /admin → 📊 Проверка ответов.",
        )


@router.callback_query(F.data.startswith("skip:"))
async def cb_skip(callback: CallbackQuery):
    await callback.answer("Ок, задание осталось в долге.", show_alert=False)


# Старые кнопки «Выполнено» из уже отправленных сообщений.
@router.callback_query(F.data.startswith("complete:"))
async def cb_complete_legacy(callback: CallbackQuery, state: FSMContext):
    try:
        aid = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    async with async_session() as session:
        a = await session.get(DailyAssignment, aid)
        if not a or a.is_completed or a.user_id != callback.from_user.id:
            await callback.answer("Уже закрыто.", show_alert=True)
            return
    await state.set_state(StudentStates.waiting_answer)
    await state.update_data(assignment_id=aid)
    await callback.message.reply("✏️ Напиши свой ответ:")
    await callback.answer()


# ─────────────────────────── Босс ───────────────────────────

@router.callback_query(F.data == "menu:boss")
async def cb_menu_boss(callback: CallbackQuery):
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        active = (await session.execute(
            select(BossFight).where(
                BossFight.user_id == user.id,
                BossFight.status == "active",
            ).order_by(BossFight.id.desc()).limit(1)
        )).scalar_one_or_none()
        topic_title = "—"
        if user.current_topic_id:
            from database.models import Topic
            topic = await session.get(Topic, user.current_topic_id)
            topic_title = topic.title if topic else "—"
        await session.commit()

    if active:
        text = (
            "🏰 <b>Босс темы</b>\n\n"
            f"Локация: <b>{html.escape(topic_title)}</b>\n"
            f"❤️ Сердца: {active.hearts}   ⚔️ HP: {active.hp}\n\n"
            "Бой уже идёт. Продолжим?"
        )
    else:
        text = (
            "🏰 <b>Босс темы</b>\n\n"
            f"Локация: <b>{html.escape(topic_title)}</b>\n\n"
            "5 удачных ответов ломают босса. 3 ошибки — и бой проигран.\n"
            "Подсказок в бою нет: только ты и задача.\n\n"
            "Готов?"
        )
    await callback.message.edit_text(
        text, reply_markup=boss_kb(active=bool(active)), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.in_({"boss:start", "boss:continue"}))
async def cb_boss_start(callback: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await _get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала /start", show_alert=True)
            return
        result = await start_boss(session, user)
        if not result["ok"]:
            await session.commit()
            await callback.answer(result["error"], show_alert=True)
            return
        fight = result["fight"]
        task = await boss_current_task(session, fight)
        if not task:
            await session.commit()
            await callback.answer("Не удалось найти задачу босса.", show_alert=True)
            return
        from database.models import Topic
        topic = await session.get(Topic, fight.topic_id)
        total = len([x for x in (fight.task_ids or "").split(",") if x.strip()])
        await session.commit()

    await state.set_state(StudentStates.waiting_boss_answer)
    await state.update_data(fight_id=fight.id)
    await _send_boss_task(callback.bot, callback.from_user.id, topic.title, fight, task, total)
    await callback.answer("Бой начался!")


async def _send_boss_task(bot, user_id: int, topic_title: str, fight: BossFight, task: Task, total: int) -> None:
    index = fight.current_index + 1
    caption = (
        f"🏰 <b>{html.escape(topic_title)}</b>\n"
        f"❤️ {fight.hearts}   ⚔️ HP {fight.hp}\n"
        f"Задание {index}/{total}\n\n"
        "<i>Отправь ответ текстом.</i>"
    )
    try:
        photo_path = render_latex(task.text)
        await bot.send_photo(
            user_id, FSInputFile(photo_path),
            caption=caption, parse_mode="HTML",
        )
    except Exception:
        await bot.send_message(
            user_id,
            f"{caption}\n\n{html.escape(task.text)}",
            parse_mode="HTML",
        )


@router.message(StudentStates.waiting_boss_answer)
async def process_boss_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    fight_id = data.get("fight_id")
    student_raw = (message.text or "").strip()
    if not fight_id or not student_raw:
        await message.answer("Ответ пустой. Напиши ответ текстом.")
        return

    async with async_session() as session:
        fight = await session.get(BossFight, fight_id)
        user = await session.get(User, message.from_user.id)
        if not fight or not user or fight.user_id != user.id:
            await state.clear()
            await message.answer("Бой не найден. Начни заново: /boss")
            return

        result = await answer_boss(session, user, fight, student_raw)
        await session.commit()

        if not result["ok"]:
            await state.clear()
            await message.answer(result["error"])
            return

        status = result["status"]
        if status == "won":
            await state.clear()
            lines = [
                "🏆 <b>Босс повержен!</b>",
                f"+{result['reward_xp']} XP, +{result['reward_coins']} 🪙",
            ]
            if not result.get("first_win"):
                lines.append("Повторная победа: награда меньше.")
            lines.append("Мастерство темы выросло.")
            await message.answer("\n".join(lines), parse_mode="HTML")
            return

        if status == "lost":
            await state.clear()
            await message.answer(
                "💀 <b>Босс выстоял.</b>\n\n"
                f"Причина: {html.escape(result.get('reason', 'задачи закончились'))}\n"
                "Это не приговор: потренируйся и возвращайся. /boss",
                parse_mode="HTML",
            )
            return

        # Бой продолжается
        next_task = result["next_task"]
        from database.models import Topic
        topic = await session.get(Topic, fight.topic_id)
        total = len([x for x in (fight.task_ids or "").split(",") if x.strip()])
        await session.commit()

    if result.get("is_correct"):
        await message.answer("✅ Точное попадание! Босс теряет HP.")
    else:
        await message.answer("❌ Мимо! Босс забирает сердце.")
    await _send_boss_task(message.bot, message.from_user.id, topic.title, fight, next_task, total)


# ─────────────────────────── Команды ───────────────────────────

@router.message(Command("status", "hero"))
async def cmd_status(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            await message.answer("Сначала /start")
            return
        text = await hero_card(session, user)
        await session.commit()
    await message.answer(text, reply_markup=back_to_menu_kb(), parse_mode="HTML")


@router.message(Command("map"))
async def cmd_map(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            await message.answer("Сначала /start")
            return
        rows = await map_data(session, user)
        text = map_text(rows)
        await session.commit()
    await message.answer(text, reply_markup=map_kb(), parse_mode="HTML")


@router.message(Command("shop"))
async def cmd_shop(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            await message.answer("Сначала /start")
            return
        text, kb = await _shop_view(session, user)
        await session.commit()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("boss"))
async def cmd_boss(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            await message.answer("Сначала /start")
            return
        active = (await session.execute(
            select(BossFight).where(
                BossFight.user_id == user.id,
                BossFight.status == "active",
            ).order_by(BossFight.id.desc()).limit(1)
        )).scalar_one_or_none()
        await session.commit()
    await message.answer(
        "🏰 Босс темы ждёт." if not active else "🏰 Бой уже идёт.",
        reply_markup=boss_kb(active=bool(active)),
    )


@router.message(Command("quest"))
async def cmd_quest(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            await message.answer("Сначала /start")
            return
        ensure_daily_reset(user)
        quest, uq = await get_daily_quest(session, user)
        today = local_today()
        count = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == user.id,
                DailyAssignment.assigned_date == today,
            )
        )).scalar() or 0
        await session.commit()

    text = (
        f"🎯 <b>{html.escape(quest.title)}</b>\n\n"
        f"{html.escape(quest.description)}\n\n"
        f"Прогресс: <b>{uq.progress}/{quest.goal_value}</b>\n"
        f"Награда: +{quest.reward_xp} XP, +{quest.reward_coins} 🪙"
    )
    await message.answer(
        text, reply_markup=quest_kb(has_assignments=count > 0), parse_mode="HTML",
    )
