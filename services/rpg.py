"""RPG-слой: персонаж, монеты, навыки, мастерство, квесты, магазин и боссы.

Главный принцип: RPG-награды выдаются только за реально решённые задачи.
Повторное решение той же задачи не даёт XP — иначе появляется ферма.
"""
from __future__ import annotations

import html
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    User, Topic, Task, DailyAssignment, CompletedTask, SkillProgress,
    TopicProgress, Quest, UserQuest, Item, Inventory, BossFight,
)
from services.answer_checker import check_answer
from services.gamification import (
    check_achievements, check_and_apply_level_up, update_streak, LEVEL_UP_XP,
)
from utils.time import local_today, utcnow, period_key


# ─────────────────────────── Константы ───────────────────────────

RANKS = [
    (1, "Ученик"),
    (5, "Подмастерье"),
    (10, "Чародей"),
    (15, "Архимаг"),
    (25, "Хранитель Формул"),
]

SKILLS = {
    "algebra": "Алгебра",
    "geometry": "Геометрия",
    "trigonometry": "Тригонометрия",
    "derivatives": "Производные",
}

SKILL_EMOJI = {
    "algebra": "🧮",
    "geometry": "📐",
    "trigonometry": "🌊",
    "derivatives": "📈",
}

XP_BY_DIFFICULTY = {"easy": 10, "medium": 15, "hard": 25}
COINS_BY_DIFFICULTY = {"easy": 5, "medium": 8, "hard": 12}

DAILY_XP_CAP = 120
DAILY_COIN_CAP = 40
DAILY_QUEST_GOAL = 3

BOSS_HP = 5
BOSS_HEARTS = 3
BOSS_TASKS = 7
BOSS_REWARD_XP = 100
BOSS_REWARD_COINS = 50
BOSS_MASTERY = 20


# ─────────────────────────── Вспомогательное ───────────────────────────

def skill_for_topic(title: str | None) -> str:
    t = (title or "").lower()
    if "производн" in t or "дифференц" in t:
        return "derivatives"
    if any(x in t for x in ("тригон", "sin", "cos", "tg", "ctg", "синус", "косинус", "тангенс", "котангенс", "аркфункц")):
        return "trigonometry"
    if "геометр" in t:
        return "geometry"
    return "algebra"


def rank_for_level(level: int) -> str:
    title = RANKS[0][1]
    for min_level, rank_title in RANKS:
        if level >= min_level:
            title = rank_title
    return title


def xp_bar(xp: int, width: int = 10) -> str:
    in_level = xp % LEVEL_UP_XP
    filled = min(width, in_level * width // LEVEL_UP_XP)
    return "█" * filled + "░" * (width - filled)


def mastery_bar(mastery: int, width: int = 10) -> str:
    mastery = max(0, min(100, mastery))
    filled = mastery * width // 100
    return "█" * filled + "░" * (width - filled)


def ensure_daily_reset(user: User) -> None:
    today = local_today()
    if user.daily_reset_date != today:
        user.daily_reset_date = today
        user.daily_xp = 0
        user.daily_coins = 0


async def _first_clear(session: AsyncSession, user_id: int, task_id: int) -> bool:
    result = await session.execute(
        select(CompletedTask.id).where(
            CompletedTask.user_id == user_id,
            CompletedTask.task_id == task_id,
            CompletedTask.first_clear.is_(True),
        ).limit(1)
    )
    return result.scalar_one_or_none() is None


async def _get_skill(session: AsyncSession, user_id: int, skill: str) -> SkillProgress:
    result = await session.execute(
        select(SkillProgress).where(
            SkillProgress.user_id == user_id,
            SkillProgress.skill == skill,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SkillProgress(user_id=user_id, skill=skill, xp=0, level=1)
        session.add(row)
        await session.flush()
    return row


async def _add_skill_xp(session: AsyncSession, user_id: int, skill: str, amount: int) -> SkillProgress:
    row = await _get_skill(session, user_id, skill)
    row.xp += max(0, amount)
    row.level = row.xp // 100 + 1
    return row


async def _get_topic_progress(session: AsyncSession, user_id: int, topic_id: int) -> TopicProgress:
    result = await session.execute(
        select(TopicProgress).where(
            TopicProgress.user_id == user_id,
            TopicProgress.topic_id == topic_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = TopicProgress(user_id=user_id, topic_id=topic_id, mastery=0, attempts=0, correct=0)
        session.add(row)
        await session.flush()
    return row


async def _register_topic_attempt(
    session: AsyncSession, user_id: int, topic_id: int, *, correct: bool, first_clear: bool, attempts: int
) -> TopicProgress:
    row = await _get_topic_progress(session, user_id, topic_id)
    row.attempts += 1
    if correct:
        row.correct += 1
        if first_clear:
            row.mastery = min(100, row.mastery + (10 if attempts <= 1 else 5))
    return row


# ─────────────────────────── Квесты и магазин ───────────────────────────

async def ensure_rpg_defaults(session: AsyncSession) -> None:
    """Создаёт базовые предметы и ежедневный квест, если их ещё нет."""
    items = [
        ("hint", "💡 Подсказка", "Показывает подсказку к задаче.", "💡", 15, "hint"),
        ("streak_freeze", "🧊 Заморозка стрика", "Сохраняет серию при одном пропуске.", "🧊", 50, "streak"),
        ("avatar_star", "⭐ Звёздный аватар", "Косметическая рамка для карточки героя.", "⭐", 100, "cosmetic"),
    ]
    for code, title, desc, emoji, price, effect in items:
        exists = (await session.execute(select(Item).where(Item.code == code))).scalar_one_or_none()
        if not exists:
            session.add(Item(
                code=code, title=title, description=desc,
                emoji=emoji, price=price, effect=effect,
            ))

    quest = (await session.execute(select(Quest).where(Quest.code == "daily_3"))).scalar_one_or_none()
    if not quest:
        session.add(Quest(
            code="daily_3",
            title="Квест дня",
            description="Реши 3 задачи и получи награду.",
            type="daily",
            goal_type="solve_tasks",
            goal_value=DAILY_QUEST_GOAL,
            reward_xp=20,
            reward_coins=15,
        ))
    await session.commit()


async def get_daily_quest(session: AsyncSession, user: User) -> tuple[Quest, UserQuest]:
    quest = (await session.execute(select(Quest).where(Quest.code == "daily_3"))).scalar_one_or_none()
    if not quest:
        quest = Quest(
            code="daily_3", title="Квест дня",
            description="Реши 3 задачи и получи награду.",
            type="daily", goal_type="solve_tasks", goal_value=DAILY_QUEST_GOAL,
            reward_xp=20, reward_coins=15,
        )
        session.add(quest)
        await session.flush()

    key = period_key()
    result = await session.execute(
        select(UserQuest).where(
            UserQuest.user_id == user.id,
            UserQuest.quest_id == quest.id,
            UserQuest.period_key == key,
        )
    )
    uq = result.scalar_one_or_none()
    if uq is None:
        uq = UserQuest(user_id=user.id, quest_id=quest.id, period_key=key, progress=0)
        session.add(uq)
        await session.flush()
    return quest, uq


async def progress_daily_quest(session: AsyncSession, user: User, amount: int = 1) -> dict | None:
    quest, uq = await get_daily_quest(session, user)
    if uq.claimed_at is not None:
        return None

    uq.progress = min(quest.goal_value, uq.progress + amount)
    if uq.progress < quest.goal_value:
        return None

    uq.completed_at = utcnow()
    uq.claimed_at = utcnow()
    user.xp += quest.reward_xp
    user.coins += quest.reward_coins
    user.daily_xp += quest.reward_xp
    user.daily_coins += quest.reward_coins
    await check_and_apply_level_up(user)
    return {
        "title": quest.title,
        "xp": quest.reward_xp,
        "coins": quest.reward_coins,
        "progress": uq.progress,
        "goal": quest.goal_value,
    }


async def buy_item(session: AsyncSession, user: User, code: str) -> dict:
    item = (await session.execute(
        select(Item).where(Item.code == code, Item.is_active.is_(True))
    )).scalar_one_or_none()
    if not item:
        return {"ok": False, "error": "Такого предмета нет."}
    if user.coins < item.price:
        return {"ok": False, "error": f"Не хватает монет: нужно {item.price}, у тебя {user.coins}."}

    user.coins -= item.price
    inv = (await session.execute(
        select(Inventory).where(
            Inventory.user_id == user.id,
            Inventory.item_id == item.id,
        )
    )).scalar_one_or_none()
    if inv:
        inv.quantity += 1
    else:
        session.add(Inventory(user_id=user.id, item_id=item.id, quantity=1))
    return {"ok": True, "item": item}


async def _take_inventory_item(session: AsyncSession, user_id: int, code: str) -> Item | None:
    result = await session.execute(
        select(Inventory, Item)
        .join(Item, Item.id == Inventory.item_id)
        .where(Inventory.user_id == user_id, Item.code == code, Inventory.quantity > 0)
        .limit(1)
    )
    row = result.first()
    if not row:
        return None
    inv, item = row
    inv.quantity -= 1
    return item


async def use_hint(session: AsyncSession, user: User, assignment: DailyAssignment, task: Task) -> dict:
    if assignment.used_hint:
        return {"ok": False, "error": "Подсказка к этой задаче уже использована."}

    item = await _take_inventory_item(session, user.id, "hint")
    paid_with = "item"
    price = 0
    if item is None:
        hint_item = (await session.execute(
            select(Item).where(Item.code == "hint", Item.is_active.is_(True))
        )).scalar_one_or_none()
        price = hint_item.price if hint_item else 15
        if user.coins < price:
            return {"ok": False, "error": f"Нужно {price} монет или предмет «Подсказка»."}
        user.coins -= price
        paid_with = "coins"

    assignment.used_hint = True
    generic = {
        "algebra": "Выпиши, что дано, и попробуй свести выражение к более простому виду.",
        "geometry": "Нарисуй чертёж и отметь на нём все известные величины.",
        "trigonometry": "Вспомни основные тождества и приведи всё к одной функции.",
        "derivatives": "Проверь, какое правило дифференцирования здесь применимо.",
    }.get(task.skill or "algebra", "Разбей задачу на маленькие шаги и запиши, что уже известно.")
    hint = task.hint or generic
    return {"ok": True, "hint": hint, "paid_with": paid_with, "price": price}


# ─────────────────────────── Завершение задачи ───────────────────────────

async def complete_task(
    session: AsyncSession,
    user: User,
    task: Task,
    assignment: DailyAssignment,
    *,
    source: str = "daily",
    manual: bool = False,
) -> dict:
    """Начисляет награду за правильное решение и обновляет весь RPG-прогресс."""
    ensure_daily_reset(user)

    first = await _first_clear(session, user.id, task.id)
    base_xp = XP_BY_DIFFICULTY.get(task.difficulty, 15)
    base_coins = COINS_BY_DIFFICULTY.get(task.difficulty, 8)

    if first:
        xp = base_xp
        coins = base_coins
    else:
        # Повтор — это практика, а не фарм.
        xp = 0
        coins = 2

    if assignment.used_hint:
        xp = int(xp * 0.7)
        coins = int(coins * 0.7)

    xp = max(0, min(xp, DAILY_XP_CAP - user.daily_xp))
    coins = max(0, min(coins, DAILY_COIN_CAP - user.daily_coins))

    level_before = user.level
    user.xp += xp
    user.daily_xp += xp
    user.coins += coins
    user.daily_coins += coins

    # Заморозка стрика: если пропущен ровно один день, предмет сохраняет серию.
    today = local_today()
    if (
        user.streak > 0
        and user.last_completed_date == today - timedelta(days=2)
        and await _take_inventory_item(session, user.id, "streak_freeze")
    ):
        user.last_completed_date = today - timedelta(days=1)

    await update_streak(user)

    skill = task.skill or "algebra"
    skill_row = await _add_skill_xp(session, user.id, skill, xp)
    topic_row = await _register_topic_attempt(
        session, user.id, task.topic_id,
        correct=True, first_clear=first,
        attempts=max(1, assignment.attempts),
    )

    session.add(CompletedTask(
        user_id=user.id,
        task_id=task.id,
        xp_earned=xp,
        attempts=max(1, assignment.attempts),
        used_hint=assignment.used_hint,
        first_clear=first,
        source=source,
        skill=skill,
        coins_earned=coins,
    ))

    await check_and_apply_level_up(user)
    completed_count = (
        await session.execute(
            select(func.count()).select_from(CompletedTask).where(
                CompletedTask.user_id == user.id,
                CompletedTask.first_clear.is_(True),
            )
        )
    ).scalar() or 0
    awarded = await check_achievements(session, user, completed_count)
    # Достижения могут дать XP — пересчитываем уровень ещё раз.
    await check_and_apply_level_up(user)

    quest_reward = await progress_daily_quest(session, user)

    assignment.is_completed = True
    assignment.is_correct = True
    assignment.completed_at = utcnow()
    assignment.answered_at = utcnow()

    return {
        "earned_xp": xp,
        "earned_coins": coins,
        "first_clear": first,
        "manual": manual,
        "skill": skill,
        "skill_level": skill_row.level,
        "mastery": topic_row.mastery,
        "leveled_up": user.level > level_before,
        "new_level": user.level,
        "awarded_achievements": awarded,
        "quest_reward": quest_reward,
    }


async def register_wrong_attempt(
    session: AsyncSession,
    user: User,
    assignment: DailyAssignment,
    task: Task,
    student_answer: str,
    *,
    max_attempts: int = 1,
) -> dict:
    ensure_daily_reset(user)
    assignment.attempts += 1
    assignment.student_answer = student_answer
    assignment.answered_at = utcnow()

    final = assignment.attempts >= max_attempts
    if final:
        assignment.is_completed = True
        assignment.is_correct = False
        assignment.completed_at = utcnow()

    await _register_topic_attempt(
        session, user.id, task.topic_id,
        correct=False, first_clear=False, attempts=assignment.attempts,
    )
    return {
        "final": final,
        "attempts": assignment.attempts,
        "remaining": max(0, max_attempts - assignment.attempts),
    }


async def submit_for_review(
    session: AsyncSession,
    user: User,
    assignment: DailyAssignment,
    task: Task,
    student_answer: str,
) -> None:
    """Задача без эталонного ответа: не даём XP, отправляем учителю."""
    ensure_daily_reset(user)
    assignment.student_answer = student_answer
    assignment.attempts += 1
    assignment.is_completed = True
    assignment.is_correct = None
    assignment.completed_at = utcnow()
    assignment.answered_at = utcnow()


async def award_bonus(session: AsyncSession, user: User, xp: int = 0, coins: int = 0) -> dict:
    """Бонус от учителя: без CompletedTask, чтобы не накручивать достижения."""
    ensure_daily_reset(user)
    level_before = user.level
    user.xp += max(0, xp)
    user.coins += max(0, coins)
    await check_and_apply_level_up(user)
    return {"xp": xp, "coins": coins, "leveled_up": user.level > level_before, "new_level": user.level}


# ─────────────────────────── Босс ───────────────────────────

async def start_boss(session: AsyncSession, user: User) -> dict:
    if not user.current_topic_id:
        return {"ok": False, "error": "Сначала нужно, чтобы учитель назначил тему."}

    active = (await session.execute(
        select(BossFight).where(
            BossFight.user_id == user.id,
            BossFight.status == "active",
        ).order_by(BossFight.id.desc()).limit(1)
    )).scalar_one_or_none()
    if active:
        return {"ok": True, "fight": active, "resumed": True}

    topic = await session.get(Topic, user.current_topic_id)
    if not topic:
        return {"ok": False, "error": "Тема не найдена."}

    tasks = list((await session.execute(
        select(Task)
        .where(
            Task.topic_id == topic.id,
            Task.target_user_id.is_(None),
            Task.answer.isnot(None),
            Task.answer != "",
        )
        .order_by(func.random())
        .limit(BOSS_TASKS)
    )).scalars().all())
    if len(tasks) < 3:
        return {"ok": False, "error": "В теме слишком мало задач для босса."}

    fight = BossFight(
        user_id=user.id,
        topic_id=topic.id,
        hp=min(BOSS_HP, len(tasks)),
        hearts=BOSS_HEARTS,
        task_ids=",".join(str(t.id) for t in tasks),
        current_index=0,
        status="active",
    )
    session.add(fight)
    await session.flush()
    return {"ok": True, "fight": fight, "resumed": False}


async def boss_current_task(session: AsyncSession, fight: BossFight) -> Task | None:
    ids = [int(x) for x in (fight.task_ids or "").split(",") if x.strip()]
    if fight.current_index >= len(ids):
        return None
    return await session.get(Task, ids[fight.current_index])


async def _boss_already_won(session: AsyncSession, user_id: int, topic_id: int) -> bool:
    result = await session.execute(
        select(BossFight.id).where(
            BossFight.user_id == user_id,
            BossFight.topic_id == topic_id,
            BossFight.status == "won",
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def answer_boss(
    session: AsyncSession,
    user: User,
    fight: BossFight,
    student_answer: str,
) -> dict:
    if fight.status != "active":
        return {"ok": False, "error": "Этот бой уже завершён."}

    task = await boss_current_task(session, fight)
    if task is None:
        fight.status = "lost"
        fight.finished_at = utcnow()
        return {"ok": True, "status": "lost", "reason": "Задачи закончились."}

    is_correct, reason = check_answer(student_answer, task.answer or "")
    if is_correct:
        fight.hp -= 1
    else:
        fight.hearts -= 1
    fight.current_index += 1

    if fight.hp <= 0:
        # Считаем до смены статуса: иначе autoflush сделает текущий бой
        # «уже победным» и первая победа получит уменьшенную награду.
        first_win = not await _boss_already_won(session, user.id, fight.topic_id)
        fight.status = "won"
        fight.finished_at = utcnow()
        reward_xp = BOSS_REWARD_XP if first_win else 20
        reward_coins = BOSS_REWARD_COINS if first_win else 10
        user.xp += reward_xp
        user.coins += reward_coins
        await _add_skill_xp(session, user.id, skill_for_topic((await session.get(Topic, fight.topic_id)).title), reward_xp // 2)
        progress = await _get_topic_progress(session, user.id, fight.topic_id)
        progress.mastery = min(100, progress.mastery + BOSS_MASTERY)
        await check_and_apply_level_up(user)
        return {
            "ok": True, "status": "won", "task": task, "is_correct": True,
            "reward_xp": reward_xp, "reward_coins": reward_coins,
            "first_win": first_win, "new_level": user.level,
        }

    if fight.hearts <= 0:
        fight.status = "lost"
        fight.finished_at = utcnow()
        return {"ok": True, "status": "lost", "task": task, "is_correct": is_correct, "reason": "Сердца закончились."}

    ids = [int(x) for x in (fight.task_ids or "").split(",") if x.strip()]
    if fight.current_index >= len(ids):
        fight.status = "lost"
        fight.finished_at = utcnow()
        return {"ok": True, "status": "lost", "task": task, "is_correct": is_correct, "reason": "Босс выстоял."}

    next_task = await boss_current_task(session, fight)
    return {
        "ok": True, "status": "active", "task": task, "is_correct": is_correct,
        "reason": reason, "next_task": next_task,
        "hp": fight.hp, "hearts": fight.hearts,
    }


# ─────────────────────────── Карточка и карта ───────────────────────────

async def hero_card(session: AsyncSession, user: User) -> str:
    ensure_daily_reset(user)
    quest, uq = await get_daily_quest(session, user)

    skills = list((await session.execute(
        select(SkillProgress).where(SkillProgress.user_id == user.id)
    )).scalars().all())
    by_skill = {s.skill: s for s in skills}

    topic = await session.get(Topic, user.current_topic_id) if user.current_topic_id else None
    topic_progress = None
    if topic:
        topic_progress = (await session.execute(
            select(TopicProgress).where(
                TopicProgress.user_id == user.id,
                TopicProgress.topic_id == topic.id,
            )
        )).scalar_one_or_none()

    lines = [
        f"🧙 <b>{html.escape(user.username or str(user.id))}</b>",
        f"🏅 Ранг: <b>{rank_for_level(user.level)}</b>",
        f"⭐ Уровень: <b>{user.level}</b>   💠 XP: <b>{user.xp}</b>",
        f"<code>{xp_bar(user.xp)}</code> {user.xp % LEVEL_UP_XP}/{LEVEL_UP_XP}",
        f"🪙 Монеты: <b>{user.coins}</b>   🔥 Стрик: <b>{user.streak}</b> дн.",
    ]

    star = (await session.execute(
        select(Inventory.id)
        .join(Item, Item.id == Inventory.item_id)
        .where(
            Inventory.user_id == user.id,
            Item.code == "avatar_star",
            Inventory.quantity > 0,
        )
        .limit(1)
    )).scalar_one_or_none()
    if star is not None:
        lines.append("⭐ <i>Звёздный аватар активен</i>")

    if topic:
        mastery = topic_progress.mastery if topic_progress else 0
        lines.append(
            f"\n🗺 Локация: <b>{html.escape(topic.title)}</b>\n"
            f"<code>{mastery_bar(mastery)}</code> {mastery}% мастерства"
        )

    lines.append("\n<b>Навыки:</b>")
    for code, title in SKILLS.items():
        row = by_skill.get(code)
        lvl = row.level if row else 1
        xp = row.xp if row else 0
        lines.append(f"{SKILL_EMOJI[code]} {title}: ур. <b>{lvl}</b> ({xp} XP)")

    lines.append(
        f"\n🎯 <b>{html.escape(quest.title)}</b>: {uq.progress}/{quest.goal_value}\n"
        f"Награда: +{quest.reward_xp} XP, +{quest.reward_coins} 🪙"
    )
    return "\n".join(lines)


async def map_data(
    session: AsyncSession,
    user: User,
    page: int | None = None,
    per_page: int = 10,
) -> dict:
    """Постраничная карта: все темы доступны, а не только окно вокруг текущей."""
    topics = list((await session.execute(
        select(Topic).order_by(Topic.order_index, Topic.id)
    )).scalars().all())
    if not topics:
        return {"rows": [], "page": 0, "pages": 1, "total": 0}

    current_idx = 0
    if user.current_topic_id:
        for i, t in enumerate(topics):
            if t.id == user.current_topic_id:
                current_idx = i
                break

    total = len(topics)
    pages = max(1, (total + per_page - 1) // per_page)
    if page is None:
        page = current_idx // per_page
    page = max(0, min(page, pages - 1))

    chunk = topics[page * per_page:(page + 1) * per_page]

    progress_rows = list((await session.execute(
        select(TopicProgress).where(TopicProgress.user_id == user.id)
    )).scalars().all())
    progress_by_topic = {p.topic_id: p for p in progress_rows}

    result = []
    for t in chunk:
        p = progress_by_topic.get(t.id)
        mastery = p.mastery if p else 0
        if t.id == user.current_topic_id:
            status = "current"
        elif mastery >= 70:
            status = "mastered"
        elif mastery > 0:
            status = "in_progress"
        else:
            status = "available"
        result.append({
            "id": t.id, "title": t.title, "emoji": t.emoji or "📚",
            "mastery": mastery, "status": status,
        })
    return {"rows": result, "page": page, "pages": pages, "total": total}


def map_text(data: dict) -> str:
    rows = data.get("rows") or []
    if not rows:
        return "🗺 Карта пуста. Учитель ещё не создал темы."

    lines = [
        "🗺 <b>Карта знаний</b>",
        f"📚 Всего тем: <b>{data['total']}</b> · "
        f"страница <b>{data['page'] + 1}/{data['pages']}</b>\n",
    ]
    for row in rows:
        mark = {
            "current": "📍",
            "mastered": "✅",
            "in_progress": "🔸",
            "available": "🌫",
        }.get(row["status"], "🌫")
        lines.append(
            f"{mark} {row['emoji']} <b>{html.escape(row['title'])}</b>\n"
            f"   <code>{mastery_bar(row['mastery'])}</code> {row['mastery']}%"
        )
    return "\n".join(lines)
