"""API учительской панели Mini App.

Все операции используют те же таблицы и сервисы, что и бот. Никаких
изменений схемы БД здесь нет: панель только читает и обновляет данные,
которые уже есть у бота.
"""
from __future__ import annotations

import html
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, cast, desc, func, or_, select, update, String

from config import ADMIN_IDS
from database.base import async_session
from database.models import (
    DailyAssignment,
    LessonSession,
    Task,
    Topic,
    User,
)
from services.daily_tasks import assign_daily_tasks
from services.gamification import LEVEL_UP_XP
from services.rpg import (
    COINS_BY_DIFFICULTY,
    XP_BY_DIFFICULTY,
    award_bonus,
    complete_task,
    rank_for_level,
    skill_for_topic,
)
from services.seeder import seed_missing
from services.tasks_importer import import_from_file
from utils.time import local_today
from webapp.deps import teacher_user_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/teacher",
    tags=["teacher"],
    dependencies=[Depends(teacher_user_id)],
)

Difficulty = Literal["easy", "medium", "hard"]


# ─────────────────────────── Модели запросов ───────────────────────────

class LessonIn(BaseModel):
    topic_id: int
    difficulty: Difficulty = "medium"


class TaskIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    answer: str | None = Field(default=None, max_length=1000)
    difficulty: Difficulty = "medium"


class TopicIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    activate: bool = False


class ImportIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


# ─────────────────────────── Вспомогательное ───────────────────────────

def _status(assignment: DailyAssignment) -> str:
    if not assignment.is_completed:
        return "pending"
    if assignment.is_correct is True:
        return "correct"
    if assignment.is_correct is False:
        return "wrong"
    return "review"


def _student_payload(
    user: User,
    *,
    topic_title: str | None = None,
    correct: int = 0,
    wrong: int = 0,
    pending: int = 0,
    debt: int = 0,
    today_total: int = 0,
    today_completed: int = 0,
) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "level": user.level,
        "rank": rank_for_level(user.level),
        "xp": user.xp,
        "xp_in_level": user.xp % LEVEL_UP_XP,
        "xp_for_level": LEVEL_UP_XP,
        "coins": user.coins,
        "streak": user.streak,
        "topic_id": user.current_topic_id,
        "topic_title": topic_title,
        "difficulty": user.current_difficulty,
        "correct": correct,
        "wrong": wrong,
        "pending": pending,
        "debt": debt,
        "today_total": today_total,
        "today_completed": today_completed,
    }


def _task_payload(task: Task) -> dict:
    return {
        "id": task.id,
        "topic_id": task.topic_id,
        "text": task.text,
        "answer": task.answer,
        "difficulty": task.difficulty,
        "skill": task.skill,
        "target_user_id": task.target_user_id,
    }


def _review_payload(
    assignment: DailyAssignment,
    user: User,
    task: Task,
) -> dict:
    return {
        "id": assignment.id,
        "student_id": user.id,
        "username": user.username,
        "student_answer": assignment.student_answer,
        "correct_answer": task.answer,
        "task_text": task.text,
        "difficulty": task.difficulty,
        "status": _status(assignment),
        "is_correct": assignment.is_correct,
        "manually_accepted": assignment.manually_accepted,
        "assigned_date": assignment.assigned_date.isoformat(),
        "answered_at": (
            assignment.answered_at.isoformat()
            if assignment.answered_at
            else None
        ),
    }


async def _notify(request: Request, user_id: int, text: str) -> bool:
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return False
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
        return True
    except Exception:
        logger.exception("Не удалось отправить сообщение ученику %s", user_id)
        return False


async def _get_student(session, student_id: int) -> User:
    user = await session.get(User, student_id)
    if not user:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    return user


async def _get_topic(session, topic_id: int) -> Topic:
    topic = await session.get(Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    return topic


# ─────────────────────────── Обзор ───────────────────────────

@router.get("/overview")
async def overview():
    today = local_today()
    async with async_session() as session:
        students_q = select(func.count()).select_from(User).where(
            User.role == "student"
        )
        if ADMIN_IDS:
            students_q = students_q.where(User.id.notin_(ADMIN_IDS))
        students = (await session.execute(students_q)).scalar() or 0
        topics = (await session.execute(
            select(func.count()).select_from(Topic)
        )).scalar() or 0
        tasks = (await session.execute(
            select(func.count()).select_from(Task)
        )).scalar() or 0
        pending_review = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.is_completed.is_(True),
                DailyAssignment.is_correct.is_(None),
            )
        )).scalar() or 0
        wrong_total = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.is_completed.is_(True),
                DailyAssignment.is_correct.is_(False),
            )
        )).scalar() or 0
        today_total = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.assigned_date == today,
            )
        )).scalar() or 0
        today_completed = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.assigned_date == today,
                DailyAssignment.is_completed.is_(True),
            )
        )).scalar() or 0
        active_topic = (await session.execute(
            select(Topic).where(Topic.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()

    return {
        "students": students,
        "topics": topics,
        "tasks": tasks,
        "pending_review": pending_review,
        "wrong_total": wrong_total,
        "today_total": today_total,
        "today_completed": today_completed,
        "active_topic": (
            {"id": active_topic.id, "title": active_topic.title}
            if active_topic else None
        ),
    }


# ─────────────────────────── Ученики ───────────────────────────

@router.get("/students")
async def students(
    query: str = "",
    limit: int = Query(100, ge=1, le=300),
):
    today = local_today()
    async with async_session() as session:
        q = select(User).where(User.role == "student")
        if ADMIN_IDS:
            q = q.where(User.id.notin_(ADMIN_IDS))
        needle = query.strip()
        if needle:
            like = f"%{needle}%"
            q = q.where(or_(
                User.username.ilike(like),
                cast(User.id, String).like(like),
            ))
        q = q.order_by(User.xp.desc()).limit(limit)
        users = list((await session.execute(q)).scalars().all())

        topic_rows = (await session.execute(
            select(Topic.id, Topic.title)
        )).all()
        topics = {tid: title for tid, title in topic_rows}

        counts = (await session.execute(
            select(
                DailyAssignment.user_id,
                func.sum(case(
                    (DailyAssignment.is_correct.is_(True), 1), else_=0,
                )).label("correct"),
                func.sum(case(
                    (DailyAssignment.is_correct.is_(False), 1), else_=0,
                )).label("wrong"),
                func.sum(case(
                    (
                        DailyAssignment.is_completed.is_(True)
                        & DailyAssignment.is_correct.is_(None),
                        1,
                    ),
                    else_=0,
                )).label("pending"),
                func.sum(case(
                    (
                        DailyAssignment.is_completed.is_(False)
                        & (DailyAssignment.assigned_date < today),
                        1,
                    ),
                    else_=0,
                )).label("debt"),
            ).group_by(DailyAssignment.user_id)
        )).all()
        by_user = {row[0]: row for row in counts}

        today_counts = (await session.execute(
            select(
                DailyAssignment.user_id,
                func.count().label("total"),
                func.sum(case(
                    (DailyAssignment.is_completed.is_(True), 1), else_=0,
                )).label("completed"),
            )
            .where(DailyAssignment.assigned_date == today)
            .group_by(DailyAssignment.user_id)
        )).all()
        today_by_user = {row[0]: row for row in today_counts}

    result = []
    for user in users:
        row = by_user.get(user.id)
        trow = today_by_user.get(user.id)
        result.append(_student_payload(
            user,
            topic_title=topics.get(user.current_topic_id),
            correct=int(row[1] or 0) if row else 0,
            wrong=int(row[2] or 0) if row else 0,
            pending=int(row[3] or 0) if row else 0,
            debt=int(row[4] or 0) if row else 0,
            today_total=int(trow[1] or 0) if trow else 0,
            today_completed=int(trow[2] or 0) if trow else 0,
        ))
    return {"students": result, "total": len(result)}


@router.get("/students/{student_id}")
async def student_detail(student_id: int):
    today = local_today()
    async with async_session() as session:
        user = await _get_student(session, student_id)
        topic = (
            await session.get(Topic, user.current_topic_id)
            if user.current_topic_id else None
        )
        correct = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == student_id,
                DailyAssignment.is_correct.is_(True),
            )
        )).scalar() or 0
        wrong = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == student_id,
                DailyAssignment.is_correct.is_(False),
            )
        )).scalar() or 0
        pending = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == student_id,
                DailyAssignment.is_completed.is_(True),
                DailyAssignment.is_correct.is_(None),
            )
        )).scalar() or 0
        debt = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == student_id,
                DailyAssignment.is_completed.is_(False),
                DailyAssignment.assigned_date < today,
            )
        )).scalar() or 0
        today_total = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == student_id,
                DailyAssignment.assigned_date == today,
            )
        )).scalar() or 0
        today_completed = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.user_id == student_id,
                DailyAssignment.assigned_date == today,
                DailyAssignment.is_completed.is_(True),
            )
        )).scalar() or 0

        recent = (await session.execute(
            select(DailyAssignment, Task)
            .join(Task, Task.id == DailyAssignment.task_id)
            .where(DailyAssignment.user_id == student_id)
            .order_by(desc(DailyAssignment.id))
            .limit(15)
        )).all()

    return {
        "student": _student_payload(
            user,
            topic_title=topic.title if topic else None,
            correct=correct,
            wrong=wrong,
            pending=pending,
            debt=debt,
            today_total=today_total,
            today_completed=today_completed,
        ),
        "recent": [
            {
                "id": assignment.id,
                "task_text": task.text,
                "status": _status(assignment),
                "assigned_date": assignment.assigned_date.isoformat(),
                "student_answer": assignment.student_answer,
                "correct_answer": task.answer,
            }
            for assignment, task in recent
        ],
    }


@router.post("/students/{student_id}/lesson")
async def set_lesson(student_id: int, body: LessonIn):
    async with async_session() as session:
        user = await _get_student(session, student_id)
        topic = await _get_topic(session, body.topic_id)
        user.current_topic_id = topic.id
        user.current_difficulty = body.difficulty
        session.add(LessonSession(
            user_id=user.id,
            topic_id=topic.id,
            difficulty=body.difficulty,
        ))
        await session.commit()
        return {
            "ok": True,
            "student_id": user.id,
            "topic": {"id": topic.id, "title": topic.title},
            "difficulty": body.difficulty,
        }


@router.post("/students/{student_id}/assign")
async def assign_tasks(student_id: int, request: Request):
    async with async_session() as session:
        user = await _get_student(session, student_id)
        if not user.current_topic_id:
            raise HTTPException(
                status_code=400,
                detail="Сначала назначь ученику тему",
            )
    bot = getattr(request.app.state, "bot", None)
    count = await assign_daily_tasks(
        bot, student_id, notify=bot is not None,
    )
    return {"ok": True, "assigned": count}


@router.post("/assign-all")
async def assign_all(request: Request):
    async with async_session() as session:
        q = select(User).where(User.role == "student")
        if ADMIN_IDS:
            q = q.where(User.id.notin_(ADMIN_IDS))
        students = list((await session.execute(q)).scalars().all())

    bot = getattr(request.app.state, "bot", None)
    sent = 0
    errors = 0
    for student in students:
        try:
            await assign_daily_tasks(
                bot, student.id, notify=bot is not None,
            )
            sent += 1
        except Exception:
            logger.exception("Не удалось выдать задания %s", student.id)
            errors += 1
    return {
        "ok": True,
        "total": len(students),
        "sent": sent,
        "errors": errors,
    }


@router.post("/students/{student_id}/individual")
async def individual_task(student_id: int, body: TaskIn):
    async with async_session() as session:
        user = await _get_student(session, student_id)
        if not user.current_topic_id:
            raise HTTPException(
                status_code=400,
                detail="Сначала назначь ученику тему",
            )
        topic = await _get_topic(session, user.current_topic_id)
        task = Task(
            topic_id=topic.id,
            text=body.text.strip(),
            answer=(body.answer or "").strip() or None,
            difficulty=body.difficulty,
            target_user_id=user.id,
            skill=skill_for_topic(topic.title),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return {"ok": True, "task": _task_payload(task)}


# ─────────────────────────── Темы и задания ───────────────────────────

@router.get("/topics")
async def topics(
    query: str = "",
    page: int = Query(0, ge=0),
    per_page: int = Query(20, ge=1, le=500),
):
    async with async_session() as session:
        base = select(Topic)
        needle = query.strip()
        if needle:
            base = base.where(Topic.title.ilike(f"%{needle}%"))
        total = (await session.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar() or 0
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        rows = list((await session.execute(
            base.order_by(Topic.title)
            .offset(page * per_page)
            .limit(per_page)
        )).scalars().all())

        task_counts = (await session.execute(
            select(Task.topic_id, func.count())
            .group_by(Task.topic_id)
        )).all()
        counts = {tid: count for tid, count in task_counts}

        active = (await session.execute(
            select(Topic.id).where(Topic.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()

    return {
        "items": [
            {
                "id": topic.id,
                "title": topic.title,
                "emoji": topic.emoji or "📚",
                "is_active": topic.is_active,
                "tasks_count": counts.get(topic.id, 0),
            }
            for topic in rows
        ],
        "total": total,
        "page": page,
        "pages": pages,
        "active_topic_id": active,
    }


@router.post("/topics")
async def create_topic(body: TopicIn):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    async with async_session() as session:
        exists = (await session.execute(
            select(Topic.id).where(Topic.title == title).limit(1)
        )).scalar_one_or_none()
        if exists:
            raise HTTPException(
                status_code=409,
                detail="Тема с таким названием уже есть",
            )
        max_order = (await session.execute(
            select(func.max(Topic.order_index))
        )).scalar() or 0
        if body.activate:
            await session.execute(update(Topic).values(is_active=False))
        topic = Topic(
            title=title,
            is_active=body.activate,
            order_index=max_order + 1,
        )
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
        return {
            "ok": True,
            "topic": {
                "id": topic.id,
                "title": topic.title,
                "emoji": topic.emoji or "📚",
                "is_active": topic.is_active,
                "tasks_count": 0,
            },
        }


@router.post("/topics/{topic_id}/activate")
async def activate_topic(topic_id: int):
    async with async_session() as session:
        topic = await _get_topic(session, topic_id)
        await session.execute(update(Topic).values(is_active=False))
        await session.execute(
            update(Topic).where(Topic.id == topic_id).values(is_active=True)
        )
        await session.commit()
        return {"ok": True, "topic_id": topic.id, "title": topic.title}


@router.get("/topics/{topic_id}/tasks")
async def topic_tasks(
    topic_id: int,
    page: int = Query(0, ge=0),
    per_page: int = Query(20, ge=1, le=100),
):
    async with async_session() as session:
        topic = await _get_topic(session, topic_id)
        total = (await session.execute(
            select(func.count()).select_from(Task).where(
                Task.topic_id == topic_id
            )
        )).scalar() or 0
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        rows = list((await session.execute(
            select(Task)
            .where(Task.topic_id == topic_id)
            .order_by(Task.difficulty, Task.id)
            .offset(page * per_page)
            .limit(per_page)
        )).scalars().all())
    return {
        "topic": {
            "id": topic.id,
            "title": topic.title,
            "emoji": topic.emoji or "📚",
            "is_active": topic.is_active,
        },
        "items": [_task_payload(task) for task in rows],
        "total": total,
        "page": page,
        "pages": pages,
    }


@router.post("/topics/{topic_id}/tasks")
async def create_task(topic_id: int, body: TaskIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст задания пустой")
    async with async_session() as session:
        topic = await _get_topic(session, topic_id)
        task = Task(
            topic_id=topic.id,
            text=text,
            answer=(body.answer or "").strip() or None,
            difficulty=body.difficulty,
            target_user_id=None,
            skill=skill_for_topic(topic.title),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return {"ok": True, "task": _task_payload(task)}


# ─────────────────────────── Проверка ответов ───────────────────────────

@router.get("/reviews")
async def reviews(
    kind: Literal["all", "pending", "wrong"] = "pending",
    student_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    async with async_session() as session:
        q = (
            select(DailyAssignment, User, Task)
            .join(User, User.id == DailyAssignment.user_id)
            .join(Task, Task.id == DailyAssignment.task_id)
            .where(DailyAssignment.is_completed.is_(True))
        )
        if kind == "pending":
            q = q.where(DailyAssignment.is_correct.is_(None))
        elif kind == "wrong":
            q = q.where(DailyAssignment.is_correct.is_(False))
        else:
            q = q.where(or_(
                DailyAssignment.is_correct.is_(False),
                DailyAssignment.is_correct.is_(None),
            ))
        if student_id is not None:
            q = q.where(DailyAssignment.user_id == student_id)
        rows = (await session.execute(
            q.order_by(desc(DailyAssignment.answered_at)).limit(limit)
        )).all()
    return {
        "items": [
            _review_payload(assignment, user, task)
            for assignment, user, task in rows
        ],
        "total": len(rows),
    }


@router.get("/reviews/{assignment_id}")
async def review_detail(assignment_id: int):
    async with async_session() as session:
        assignment = await session.get(DailyAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Ответ не найден")
        user = await _get_student(session, assignment.user_id)
        task = await session.get(Task, assignment.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        return _review_payload(assignment, user, task)


@router.post("/reviews/{assignment_id}/accept")
async def accept_review(assignment_id: int, request: Request):
    async with async_session() as session:
        assignment = await session.get(DailyAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Ответ не найден")
        if assignment.is_correct is True:
            raise HTTPException(status_code=409, detail="Ответ уже засчитан")
        user = await _get_student(session, assignment.user_id)
        task = await session.get(Task, assignment.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Задание не найдено")

        result = await complete_task(
            session, user, task, assignment,
            source="manual", manual=True,
        )
        assignment.manually_accepted = True
        await session.commit()

        message = (
            "✅ <b>Учитель засчитал твой ответ как правильный!</b>\n"
            f"Задание: «{html.escape(task.text[:100])}"
            f"{'…' if len(task.text) > 100 else ''}»\n"
            f"Твой ответ: <code>{html.escape(assignment.student_answer or '—')}</code>\n\n"
            f"+{result['earned_xp']} XP, +{result['earned_coins']} 🪙"
        )
        if result["leveled_up"]:
            message += f"\n🎉 Новый уровень: <b>{result['new_level']}</b>!"
        for achievement in result["awarded_achievements"]:
            message += f"\n🏆 {html.escape(achievement.title)}"

    notified = await _notify(request, user.id, message)
    return {
        "ok": True,
        "notified": notified,
        "reward": {
            "xp": result["earned_xp"],
            "coins": result["earned_coins"],
            "leveled_up": result["leveled_up"],
            "new_level": result["new_level"],
            "achievements": [
                achievement.title
                for achievement in result["awarded_achievements"]
            ],
        },
    }


@router.post("/reviews/{assignment_id}/bonus")
async def bonus_review(assignment_id: int, request: Request):
    async with async_session() as session:
        assignment = await session.get(DailyAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Ответ не найден")
        user = await _get_student(session, assignment.user_id)
        task = await session.get(Task, assignment.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Задание не найдено")

        result = await award_bonus(
            session,
            user,
            xp=XP_BY_DIFFICULTY.get(task.difficulty, 15),
            coins=COINS_BY_DIFFICULTY.get(task.difficulty, 8),
        )
        await session.commit()

        message = (
            "🎁 <b>Учитель начислил тебе бонус</b>\n"
            f"+{result['xp']} XP, +{result['coins']} 🪙 "
            "за усилие по заданию"
        )
        if result["leveled_up"]:
            message += f"\n🎉 Новый уровень: <b>{result['new_level']}</b>!"

    notified = await _notify(request, user.id, message)
    return {
        "ok": True,
        "notified": notified,
        "xp": result["xp"],
        "coins": result["coins"],
    }


# ─────────────────────────── Импорт и наполнение ───────────────────────────

@router.post("/import")
async def import_tasks(body: ImportIn):
    stats = await import_from_file(body.content)
    return stats


@router.post("/seed")
async def seed_data():
    stats = await seed_missing(force=True)
    return stats
