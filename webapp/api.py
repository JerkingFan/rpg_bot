"""HTTP API для Telegram Mini App.

Использует ту же базу и ту же бизнес-логику, что и бот: XP, монеты,
мастерство, квесты, магазин и боссы. Клиент не может влиять на проверку
ответов — она всегда выполняется на сервере.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiogram import Bot

from database.base import async_session
from database.models import (
    Achievement,
    BossFight,
    DailyAssignment,
    Inventory,
    Item,
    SkillProgress,
    Task,
    Topic,
    TopicProgress,
    User,
    UserAchievement,
)
from services.answer_checker import check_answer
from services.daily_tasks import assign_daily_tasks
from services.gamification import LEVEL_UP_XP
from services.rpg import (
    DAILY_COIN_CAP,
    DAILY_XP_CAP,
    RANK_ICONS,
    SKILL_EMOJI,
    SKILLS,
    answer_boss,
    boss_current_task,
    buy_item,
    complete_task,
    ensure_daily_reset,
    get_daily_quest,
    map_data,
    rank_for_level,
    register_wrong_attempt,
    start_boss,
    submit_for_review,
    use_hint,
)
from utils.time import local_today
from webapp.deps import current_user_id, is_teacher
from webapp.deps import require_user as _require_user
from webapp.teacher import router as teacher_router

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ─────────────────────────── Модели запросов ───────────────────────────

class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)


class BuyIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)


# ─────────────────────────── Сериализация ───────────────────────────

def _user_stats(user: User) -> dict:
    rank = rank_for_level(user.level)
    return {
        "id": user.id,
        "username": user.username,
        "xp": user.xp,
        "level": user.level,
        "rank": rank,
        "rank_icon": RANK_ICONS.get(rank, "🌑"),
        "xp_in_level": user.xp % LEVEL_UP_XP,
        "xp_for_level": LEVEL_UP_XP,
        "coins": user.coins,
        "streak": user.streak,
        "daily_xp": user.daily_xp,
        "daily_xp_cap": DAILY_XP_CAP,
        "daily_coins": user.daily_coins,
        "daily_coins_cap": DAILY_COIN_CAP,
    }


def _assignment_status(assignment: DailyAssignment) -> str:
    if not assignment.is_completed:
        return "pending"
    if assignment.is_correct is True:
        return "correct"
    if assignment.is_correct is False:
        return "wrong"
    return "review"


def _task_payload(assignment: DailyAssignment, task: Task) -> dict:
    status = _assignment_status(assignment)
    today = local_today()
    return {
        "id": assignment.id,
        "task_id": task.id,
        "text": task.text,
        "difficulty": task.difficulty,
        "skill": task.skill,
        "skill_title": SKILLS.get(task.skill or "algebra", task.skill or "Алгебра"),
        "skill_emoji": SKILL_EMOJI.get(task.skill or "algebra", "🧮"),
        "status": status,
        "is_debt": assignment.assigned_date < today,
        "assigned_date": assignment.assigned_date.isoformat(),
        "used_hint": assignment.used_hint,
        "hint": task.hint if assignment.used_hint else None,
        "correct_answer": task.answer if assignment.is_completed else None,
        "student_answer": assignment.student_answer,
        "answer_available": bool((task.answer or "").strip()),
    }


def _boss_task_payload(task: Task | None) -> dict | None:
    if task is None:
        return None
    return {
        "task_id": task.id,
        "text": task.text,
        "difficulty": task.difficulty,
        "skill": task.skill,
        "skill_title": SKILLS.get(task.skill or "algebra", task.skill or "Алгебра"),
    }


def _fight_payload(fight: BossFight) -> dict:
    total = len([x for x in (fight.task_ids or "").split(",") if x.strip()])
    return {
        "id": fight.id,
        "hp": fight.hp,
        "hearts": fight.hearts,
        "current_index": fight.current_index,
        "total": total,
        "status": fight.status,
    }


def _inventory_map(rows) -> dict[str, int]:
    return {code: qty for code, qty in rows}


# ─────────────────────────── Данные ───────────────────────────

async def _me_payload(session: AsyncSession, user: User) -> dict:
    ensure_daily_reset(user)
    quest, user_quest = await get_daily_quest(session, user)

    skills_rows = list((await session.execute(
        select(SkillProgress).where(SkillProgress.user_id == user.id)
    )).scalars().all())
    by_skill = {row.skill: row for row in skills_rows}
    skills = [
        {
            "code": code,
            "title": title,
            "emoji": SKILL_EMOJI.get(code, "✨"),
            "level": by_skill[code].level if code in by_skill else 1,
            "xp": by_skill[code].xp if code in by_skill else 0,
        }
        for code, title in SKILLS.items()
    ]

    topic = None
    if user.current_topic_id:
        topic_row = await session.get(Topic, user.current_topic_id)
        if topic_row:
            progress = (await session.execute(
                select(TopicProgress).where(
                    TopicProgress.user_id == user.id,
                    TopicProgress.topic_id == topic_row.id,
                )
            )).scalar_one_or_none()
            topic = {
                "id": topic_row.id,
                "title": topic_row.title,
                "emoji": topic_row.emoji or "📚",
                "mastery": progress.mastery if progress else 0,
                "difficulty": user.current_difficulty,
            }

    achievements = [
        {"title": title, "description": description}
        for title, description in (await session.execute(
            select(Achievement.title, Achievement.description)
            .join(UserAchievement, UserAchievement.achievement_id == Achievement.id)
            .where(UserAchievement.user_id == user.id)
            .order_by(UserAchievement.unlocked_at.desc())
        )).all()
    ]

    today = local_today()
    today_rows = list((await session.execute(
        select(DailyAssignment).where(
            DailyAssignment.user_id == user.id,
            DailyAssignment.assigned_date == today,
        )
    )).scalars().all())
    debt_count = (await session.execute(
        select(func.count()).select_from(DailyAssignment).where(
            DailyAssignment.user_id == user.id,
            DailyAssignment.is_completed.is_(False),
            DailyAssignment.assigned_date < today,
        )
    )).scalar() or 0

    active_boss = (await session.execute(
        select(BossFight).where(
            BossFight.user_id == user.id,
            BossFight.status == "active",
        ).order_by(BossFight.id.desc()).limit(1)
    )).scalar_one_or_none()

    await session.commit()

    return {
        "user": _user_stats(user),
        "is_teacher": is_teacher(user),
        "topic": topic,
        "skills": skills,
        "achievements": achievements,
        "quest": {
            "title": quest.title,
            "description": quest.description,
            "progress": user_quest.progress,
            "goal": quest.goal_value,
            "reward_xp": quest.reward_xp,
            "reward_coins": quest.reward_coins,
            "completed": user_quest.claimed_at is not None,
        },
        "today": {
            "total": len(today_rows),
            "completed": sum(1 for row in today_rows if row.is_completed),
            "correct": sum(1 for row in today_rows if row.is_correct is True),
            "wrong": sum(1 for row in today_rows if row.is_correct is False),
            "debt": debt_count,
        },
        "boss_active": active_boss is not None,
    }


async def _tasks_payload(session: AsyncSession, user_id: int) -> list[dict]:
    today = local_today()
    rows = (await session.execute(
        select(DailyAssignment, Task)
        .join(Task, Task.id == DailyAssignment.task_id)
        .where(
            DailyAssignment.user_id == user_id,
            or_(
                DailyAssignment.assigned_date == today,
                (
                    (DailyAssignment.assigned_date < today)
                    & (DailyAssignment.is_completed.is_(False))
                ),
            ),
        )
        .order_by(DailyAssignment.assigned_date, DailyAssignment.id)
    )).all()
    return [_task_payload(assignment, task) for assignment, task in rows]


async def _shop_payload(session: AsyncSession, user: User) -> dict:
    items = list((await session.execute(
        select(Item).where(Item.is_active.is_(True)).order_by(Item.price)
    )).scalars().all())
    inventory = _inventory_map((await session.execute(
        select(Item.code, Inventory.quantity)
        .join(Inventory, Inventory.item_id == Item.id)
        .where(Inventory.user_id == user.id)
    )).all())
    return {
        "coins": user.coins,
        "items": [
            {
                "code": item.code,
                "title": item.title,
                "description": item.description,
                "emoji": item.emoji,
                "price": item.price,
                "effect": item.effect,
                "quantity": inventory.get(item.code, 0),
            }
            for item in items
        ],
    }


# ─────────────────────────── API ───────────────────────────

def create_app(bot: Bot | None = None) -> FastAPI:
    app = FastAPI(
        title="Reborn: Испытание",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Бот нужен только для уведомлений ученикам из учительской панели.
    # В локальном режиме его может не быть — API продолжит работать.
    app.state.bot = bot
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/me")
    async def api_me(user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            return await _me_payload(session, user)

    @app.get("/api/tasks")
    async def api_tasks(user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            await _require_user(session, user_id)
            tasks = await _tasks_payload(session, user_id)
            await session.commit()
            return {"tasks": tasks}

    @app.post("/api/tasks/request")
    async def api_request_tasks(user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            await _require_user(session, user_id)
            today = local_today()
            count = (await session.execute(
                select(func.count()).select_from(DailyAssignment).where(
                    DailyAssignment.user_id == user_id,
                    DailyAssignment.assigned_date == today,
                )
            )).scalar() or 0

        if count == 0:
            await assign_daily_tasks(None, user_id, notify=False)

        async with async_session() as session:
            tasks = await _tasks_payload(session, user_id)
            await session.commit()
            return {"tasks": tasks}

    @app.post("/api/tasks/{assignment_id}/answer")
    async def api_answer(
        assignment_id: int,
        body: AnswerIn,
        user_id: int = Depends(current_user_id),
    ):
        answer = body.answer.strip()
        if not answer:
            raise HTTPException(status_code=400, detail="Ответ пустой")

        async with async_session() as session:
            user = await _require_user(session, user_id)
            assignment = await session.get(DailyAssignment, assignment_id)
            if not assignment or assignment.user_id != user_id:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            if assignment.is_completed:
                raise HTTPException(status_code=409, detail="Задание уже закрыто")

            task = await session.get(Task, assignment.task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Задание не найдено")

            if not (task.answer or "").strip():
                await submit_for_review(session, user, assignment, task, answer)
                await session.commit()
                return {
                    "status": "review",
                    "message": "Ответ отправлен учителю на проверку.",
                    "user": _user_stats(user),
                }

            is_correct, _reason = check_answer(answer, task.answer or "")
            if is_correct:
                result = await complete_task(session, user, task, assignment)
                await session.commit()
                return {
                    "status": "correct",
                    "correct_answer": task.answer,
                    "reward": {
                        "xp": result["earned_xp"],
                        "coins": result["earned_coins"],
                        "first_clear": result["first_clear"],
                        "skill": result["skill"],
                        "skill_level": result["skill_level"],
                        "mastery": result["mastery"],
                        "leveled_up": result["leveled_up"],
                        "new_level": result["new_level"],
                        "achievements": [a.title for a in result["awarded_achievements"]],
                        "quest_reward": result["quest_reward"],
                    },
                    "user": _user_stats(user),
                }

            result = await register_wrong_attempt(
                session, user, assignment, task, answer, max_attempts=1,
            )
            await session.commit()
            return {
                "status": "wrong",
                "correct_answer": task.answer,
                "attempts": result["attempts"],
                "remaining": result["remaining"],
                "user": _user_stats(user),
            }

    @app.post("/api/tasks/{assignment_id}/hint")
    async def api_hint(
        assignment_id: int,
        user_id: int = Depends(current_user_id),
    ):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            assignment = await session.get(DailyAssignment, assignment_id)
            if not assignment or assignment.user_id != user_id:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            if assignment.is_completed:
                raise HTTPException(status_code=409, detail="Задание уже закрыто")

            task = await session.get(Task, assignment.task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Задание не найдено")

            result = await use_hint(session, user, assignment, task)
            await session.commit()
            if not result["ok"]:
                raise HTTPException(status_code=400, detail=result["error"])
            return {
                "hint": result["hint"],
                "paid_with": result["paid_with"],
                "price": result["price"],
                "coins": user.coins,
            }

    @app.get("/api/map")
    async def api_map(
        page: int = Query(0, ge=0),
        user_id: int = Depends(current_user_id),
    ):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            data = await map_data(session, user, page=page, per_page=500)
            await session.commit()
            return {**data, "current_topic_id": user.current_topic_id}

    @app.get("/api/shop")
    async def api_shop(user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            data = await _shop_payload(session, user)
            await session.commit()
            return data

    @app.post("/api/shop/buy")
    async def api_buy(body: BuyIn, user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            result = await buy_item(session, user, body.code)
            if not result["ok"]:
                await session.commit()
                raise HTTPException(status_code=400, detail=result["error"])
            await session.commit()
            data = await _shop_payload(session, user)
            return {
                "ok": True,
                "item": result["item"].title,
                "coins": user.coins,
                "items": data["items"],
            }

    @app.get("/api/boss")
    async def api_boss(user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            fight = (await session.execute(
                select(BossFight).where(
                    BossFight.user_id == user.id,
                    BossFight.status == "active",
                ).order_by(BossFight.id.desc()).limit(1)
            )).scalar_one_or_none()

            topic = await session.get(Topic, user.current_topic_id) if user.current_topic_id else None
            if fight:
                task = await boss_current_task(session, fight)
                await session.commit()
                return {
                    "active": True,
                    "fight": _fight_payload(fight),
                    "task": _boss_task_payload(task),
                    "topic": topic.title if topic else "—",
                }

            await session.commit()
            return {
                "active": False,
                "fight": None,
                "task": None,
                "topic": topic.title if topic else "—",
                "can_start": bool(topic),
            }

    @app.post("/api/boss/start")
    async def api_boss_start(user_id: int = Depends(current_user_id)):
        async with async_session() as session:
            user = await _require_user(session, user_id)
            result = await start_boss(session, user)
            if not result["ok"]:
                await session.commit()
                raise HTTPException(status_code=400, detail=result["error"])

            fight = result["fight"]
            task = await boss_current_task(session, fight)
            topic = await session.get(Topic, fight.topic_id)
            await session.commit()
            return {
                "ok": True,
                "fight": _fight_payload(fight),
                "task": _boss_task_payload(task),
                "topic": topic.title if topic else "—",
                "resumed": result.get("resumed", False),
            }

    @app.post("/api/boss/{fight_id}/answer")
    async def api_boss_answer(
        fight_id: int,
        body: AnswerIn,
        user_id: int = Depends(current_user_id),
    ):
        answer = body.answer.strip()
        if not answer:
            raise HTTPException(status_code=400, detail="Ответ пустой")

        async with async_session() as session:
            user = await _require_user(session, user_id)
            fight = await session.get(BossFight, fight_id)
            if not fight or fight.user_id != user_id:
                raise HTTPException(status_code=404, detail="Бой не найден")

            result = await answer_boss(session, user, fight, answer)
            await session.commit()
            if not result["ok"]:
                raise HTTPException(status_code=400, detail=result["error"])

            answered = result.get("task")
            payload = {
                "status": result["status"],
                "is_correct": result.get("is_correct"),
                "fight": _fight_payload(fight),
                "answered_task": _boss_task_payload(answered),
                "correct_answer": answered.answer if answered else None,
                "user": _user_stats(user),
            }
            if result["status"] == "active":
                payload["next_task"] = _boss_task_payload(result.get("next_task"))
            elif result["status"] == "won":
                payload["reward"] = {
                    "xp": result["reward_xp"],
                    "coins": result["reward_coins"],
                    "first_win": result["first_win"],
                }
            elif result["status"] == "lost":
                payload["reason"] = result.get("reason", "Босс выстоял.")
            return payload

    app.include_router(teacher_router)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
