import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import BOT_TOKEN, validate_config
from database.base import async_session, init_db
from handlers import common, student, teacher, admin_panel
from scheduler import setup_scheduler
from services.rpg import ensure_rpg_defaults
from services.seeder import seed_if_empty

logging.basicConfig(level=logging.INFO)

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="hero", description="Карточка героя"),
    BotCommand(command="quest", description="Квест дня"),
    BotCommand(command="map", description="Карта знаний"),
    BotCommand(command="shop", description="Магазин"),
    BotCommand(command="boss", description="Босс темы"),
    BotCommand(command="help", description="Помощь"),
]


async def main():
    validate_config()
    await init_db()
    try:
        seeded = await seed_if_empty()
    except Exception:
        logging.exception("Не удалось автоматически наполнить базу темами и заданиями")
        seeded = None
    if seeded and (seeded["topics_created"] or seeded["tasks_added"]):
        logging.info(
            "База наполнена: тем +%s, заданий +%s",
            seeded["topics_created"], seeded["tasks_added"],
        )
    async with async_session() as session:
        await ensure_rpg_defaults(session)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(common.router)
    dp.include_router(student.router)
    dp.include_router(teacher.router)
    dp.include_router(admin_panel.router)

    await bot.set_my_commands(COMMANDS)
    setup_scheduler(bot)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
