import asyncio
import contextlib
import logging
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import (
    BOT_TOKEN,
    DATA_DIR,
    DATABASE_URL,
    PORT,
    WEBAPP_URL,
    validate_config,
)
from database.backup import (
    backup_sqlite_db,
    ensure_db_dir,
    migrate_legacy_db,
)
from database.base import async_session, init_db
from handlers import common, student, teacher, admin_panel
from scheduler import setup_scheduler
from services.rpg import ensure_rpg_defaults
from services.seeder import seed_if_empty
from webapp.api import create_app

logging.basicConfig(level=logging.INFO)

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="app", description="Открыть приложение"),
    BotCommand(command="hero", description="Карточка героя"),
    BotCommand(command="quest", description="Квест дня"),
    BotCommand(command="map", description="Карта знаний"),
    BotCommand(command="shop", description="Магазин"),
    BotCommand(command="boss", description="Босс темы"),
    BotCommand(command="help", description="Помощь"),
]


async def main():
    validate_config()
    db_path = ensure_db_dir()
    logging.info(
        "База данных: %s",
        db_path.resolve() if db_path else DATABASE_URL,
    )
    if DATA_DIR and db_path is not None:
        persistent_root = Path(DATA_DIR).resolve()
        if persistent_root not in db_path.resolve().parents:
            logging.warning(
                "База лежит вне постоянного каталога %s — прогресс может "
                "потеряться при деплое. Укажи DATABASE_URL внутри DATA_DIR.",
                persistent_root,
            )
    try:
        migrate_legacy_db(db_path)
    except Exception:
        logging.exception("Не удалось перенести старую базу в постоянный каталог")
    try:
        backup_sqlite_db()
    except Exception:
        logging.exception("Не удалось сделать резервную копию базы")
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

    if WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🎮 Приложение",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            )
        except Exception:
            logging.exception("Не удалось установить кнопку Mini App в Telegram")

    web_task = None
    web_server = None
    try:
        web_server = uvicorn.Server(
            uvicorn.Config(
                create_app(bot),
                host="0.0.0.0",
                port=PORT,
                log_level="info",
                log_config=None,
                access_log=False,
            )
        )
        # Сигналы обрабатывает aiogram, uvicorn работает как обычная задача.
        # В новых версиях uvicorn флага install_signal_handlers уже нет,
        # поэтому дополнительно отключаем перехват сигналов.
        web_server.install_signal_handlers = False
        web_server.capture_signals = lambda: contextlib.nullcontext()
        web_task = asyncio.create_task(web_server.serve(), name="webapp")
        logging.info(
            "Mini App слушает 0.0.0.0:%s (публичный URL: %s)",
            PORT, WEBAPP_URL or "не задан",
        )

        def _web_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            error = task.exception()
            if error is not None:
                logging.error(
                    "Mini App остановился с ошибкой", exc_info=error,
                )

        web_task.add_done_callback(_web_done)
    except Exception:
        logging.exception("Не удалось запустить Mini App, бот продолжит работу")

    try:
        await dp.start_polling(bot)
    finally:
        if web_server is not None:
            web_server.should_exit = True
        if web_task is not None:
            try:
                await asyncio.wait_for(web_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                web_task.cancel()
            except Exception:
                logging.exception("Ошибка при остановке Mini App")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
