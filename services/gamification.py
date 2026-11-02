from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Achievement, UserAchievement
from utils.time import local_today

XP_PER_TASK = 10
XP_BY_DIFFICULTY = {"easy": 10, "medium": 15, "hard": 25}
LEVEL_UP_XP = 100
STREAK_BONUS_THRESHOLD = 3
STREAK_BONUS_MULTIPLIER = 1.5


async def check_and_apply_level_up(user: User) -> bool:
    new_level = user.xp // LEVEL_UP_XP + 1
    if new_level > user.level:
        user.level = new_level
        return True
    return False


async def update_streak(user: User) -> None:
    today = local_today()
    if user.last_completed_date == today:
        return
    if user.last_completed_date == today - timedelta(days=1):
        user.streak += 1
    else:
        user.streak = 1
    user.last_completed_date = today


async def calculate_xp(user: User, base_xp: int = XP_PER_TASK, difficulty: str = "medium") -> int:
    base = XP_BY_DIFFICULTY.get(difficulty, base_xp)
    multiplier = STREAK_BONUS_MULTIPLIER if user.streak >= STREAK_BONUS_THRESHOLD else 1.0
    return int(base * multiplier)


async def award_xp_for_task(session, user, task, *, reason: str = "выполнено") -> dict:
    """
    Начисляет XP за выполненную задачу, обновляет стрик, уровень, достижения.
    Возвращает dict: {earned, leveled_up, awarded_achievements, new_level, reason}
    """
    from database.models import CompletedTask
    from sqlalchemy import select, func

    await update_streak(user)
    earned = await calculate_xp(user, difficulty=task.difficulty)
    user.xp += earned

    session.add(CompletedTask(user_id=user.id, task_id=task.id, xp_earned=earned))
    leveled_up = await check_and_apply_level_up(user)

    completed_count = (
        await session.execute(
            select(func.count()).select_from(CompletedTask).where(
                CompletedTask.user_id == user.id
            )
        )
    ).scalar() or 0
    awarded = await check_achievements(session, user, completed_count)

    return {
        "earned": earned,
        "leveled_up": leveled_up,
        "awarded_achievements": awarded,
        "new_level": user.level,
        "reason": reason,
    }


async def check_achievements(
    session: AsyncSession, user: User, completed_count: int
) -> list[Achievement]:
    awarded = []

    candidates = []
    if completed_count >= 1:
        candidates.append(("first_task", "🎯 Первое задание", "Выполнено первое задание", 20))
    if completed_count >= 5:
        candidates.append(("five_tasks", "🔥 Пять заданий", "Выполнено 5 заданий", 50))
    if completed_count >= 10:
        candidates.append(("marathon", "🏃 Марафонец", "Выполнено 10 заданий", 100))
    if user.streak >= 3:
        candidates.append(("streak_3", "⚡ Серия 3 дня", "3 дня подряд с заданиями", 30))
    if user.level >= 5:
        candidates.append(("level_5", "⭐ Уровень 5", "Достигнут 5 уровень", 0))

    for code, title, desc, bonus in candidates:
        existing = await session.execute(
            select(UserAchievement)
            .join(Achievement)
            .where(UserAchievement.user_id == user.id, Achievement.code == code)
        )
        if existing.scalar_one_or_none():
            continue

        ach_result = await session.execute(
            select(Achievement).where(Achievement.code == code)
        )
        achievement = ach_result.scalar_one_or_none()
        if not achievement:
            achievement = Achievement(
                code=code, title=title, description=desc, xp_bonus=bonus
            )
            session.add(achievement)
            await session.flush()

        ua = UserAchievement(user_id=user.id, achievement_id=achievement.id)
        session.add(ua)
        if achievement.xp_bonus:
            user.xp += achievement.xp_bonus
        awarded.append(achievement)

    return awarded
