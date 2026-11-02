from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from config import DAILY_TASK_TIME
from database.base import async_session
from database.models import User
from keyboards.inline import main_menu_kb

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    tg_user = message.from_user
    async with async_session() as session:
        user = await session.get(User, tg_user.id)
        if not user:
            user = User(
                id=tg_user.id,
                username=tg_user.username,
                role="student",
            )
            session.add(user)
            await session.commit()
            greeting = (
                "👋 Привет! Ты теперь <b>ученик</b>.\n"
                "Учитель скоро назначит тебе тему и задания.\n\n"
            )
        else:
            if user.username != tg_user.username:
                user.username = tg_user.username
                await session.commit()
            greeting = "👋 С возвращением!\n\n"

    await message.answer(
        greeting
        + "Каждый день я присылаю задания. Решай их, получай XP и монеты, "
        "прокачивай навыки и открывай новые локации.\n\n"
        "<b>Твоя петля:</b> квест дня → задачи → награда → карта → босс темы.\n\n"
        "<b>Команды:</b>\n"
        "/hero — карточка героя\n"
        "/quest — квест дня\n"
        "/map — карта знаний\n"
        "/shop — магазин\n"
        "/boss — босс темы\n"
        "/help — помощь",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Как это работает:</b>\n"
        f"• Каждый день в {DAILY_TASK_TIME} я присылаю квест дня.\n"
        "• За правильный ответ дают XP, монеты и мастерство темы.\n"
        "• 100 XP = новый уровень. Каждые 5 уровней — новый ранг.\n"
        "• Повторное решение той же задачи XP не даёт — это практика.\n"
        "• Ошибся — есть вторая попытка. После неё покажу ответ.\n"
        "• Подсказка стоит монеты и немного снижает награду.\n"
        "• Босс темы — проверка мастерства: 5 HP и 3 сердца.\n\n"
        "/hero — герой, /quest — квест, /map — карта, /shop — магазин, /boss — босс",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(
        "🎛 <b>Главное меню</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
