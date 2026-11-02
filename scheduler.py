import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from sqlalchemy import select

from config import DAILY_TASK_TIME, ADMIN_IDS, TIMEZONE
from database.base import async_session
from database.models import User
from services.daily_tasks import assign_daily_tasks

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone=TIMEZONE)


def setup_scheduler(bot: Bot):
    hour, minute = map(int, DAILY_TASK_TIME.split(":"))

    @scheduler.scheduled_job(
        "cron",
        hour=hour,
        minute=minute,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    async def daily_job():
        logger.info("Запуск ежедневной выдачи заданий")
        async with async_session() as session:
            query = select(User).where(User.role == "student")
            if ADMIN_IDS:
                query = query.where(User.id.notin_(ADMIN_IDS))
            students = list((await session.execute(query)).scalars().all())

        for student in students:
            try:
                await assign_daily_tasks(bot, student.id)
            except Exception as e:
                logger.exception(f"Ошибка при выдаче ученику {student.id}: {e}")

    scheduler.start()
    logger.info(f"Планировщик запущен. Ежедневная выдача в {DAILY_TASK_TIME} ({TIMEZONE}).")
