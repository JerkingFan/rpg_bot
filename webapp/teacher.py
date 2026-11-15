"""API учительской панели Mini App.

Все операции используют те же таблицы и сервисы, что и бот. Никаких
изменений схемы БД здесь нет: панель только читает и обновляет данные,
которые уже есть у бота.
"""
from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, cast, desc, func, or_, select, update, String

from config import ADMIN_IDS
from database.base import async_session
from database.models import (
    BossFight,
    CompletedTask,
    DailyAssignment,
    LessonSession,
    Task,
    Topic,
    TopicProgress,
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


class BonusIn(BaseModel):
    xp: int = Field(0, ge=0, le=100_000)
    coins: int = Field(0, ge=0, le=100_000)
    comment: str | None = Field(default=None, max_length=500)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class BroadcastIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    audience: Literal["all", "debt", "pending", "today_open"] = "all"


# ─────────────────────────── Вспомогательное ───────────────────────────

def _status(assignment: DailyAssignment) -> str:
    if not assignment.is_completed:
        return "pending"
    if assignment.is_correct is True:
        return "correct"
    if assignment.is_correct is False:
        return "wrong"
    return "review"


def _board_status(today_total: int, today_completed: int) -> str:
    if today_total <= 0:
        return "no_tasks"
    if today_completed >= today_total:
        return "done"
    if today_completed > 0:
        return "partial"
    return "none"


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
    today_correct: int = 0,
    today_wrong: int = 0,
    today_pending: int = 0,
    last_activity: datetime | None = None,
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
        "daily_xp": user.daily_xp,
        "daily_coins": user.daily_coins,
        "topic_id": user.current_topic_id,
        "topic_title": topic_title,
        "difficulty": user.current_difficulty,
        "correct": correct,
        "wrong": wrong,
        "pending": pending,
        "debt": debt,
        "today_total": today_total,
        "today_completed": today_completed,
        "today_correct": today_correct,
        "today_wrong": today_wrong,
        "today_pending": today_pending,
        "status": _board_status(today_total, today_completed),
        "last_activity": last_activity.isoformat() if last_activity else None,
    }


async def _student_metrics(session, today):
    """Общие счётчики по каждому ученику (без сегодняшних)."""
    rows = (await session.execute(
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
            func.max(DailyAssignment.answered_at).label("last_activity"),
        ).group_by(DailyAssignment.user_id)
    )).all()
    return {row[0]: row for row in rows}


async def _today_metrics(session, today):
    """Счётчики только за сегодня."""
    rows = (await session.execute(
        select(
            DailyAssignment.user_id,
            func.count().label("total"),
            func.sum(case(
                (DailyAssignment.is_completed.is_(True), 1), else_=0,
            )).label("completed"),
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
        )
        .where(DailyAssignment.assigned_date == today)
        .group_by(DailyAssignment.user_id)
    )).all()
    return {row[0]: row for row in rows}


def _student_from_rows(
    user: User,
    topics: dict[int, str],
    metrics: dict,
    today_metrics: dict,
) -> dict:
    row = metrics.get(user.id)
    trow = today_metrics.get(user.id)
    return _student_payload(
        user,
        topic_title=topics.get(user.current_topic_id),
        correct=int(row[1] or 0) if row else 0,
        wrong=int(row[2] or 0) if row else 0,
        pending=int(row[3] or 0) if row else 0,
        debt=int(row[4] or 0) if row else 0,
        last_activity=row[5] if row else None,
        today_total=int(trow[1] or 0) if trow else 0,
        today_completed=int(trow[2] or 0) if trow else 0,
        today_correct=int(trow[3] or 0) if trow else 0,
        today_wrong=int(trow[4] or 0) if trow else 0,
        today_pending=int(trow[5] or 0) if trow else 0,
    )


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


# ─────────────────────────── Табло ───────────────────────────

@router.get("/dashboard")
async def dashboard():
    today = local_today()
    week_start = today - timedelta(days=6)
    async with async_session() as session:
        q = select(User).where(User.role == "student")
        if ADMIN_IDS:
            q = q.where(User.id.notin_(ADMIN_IDS))
        users = list((await session.execute(
            q.order_by(User.xp.desc())
        )).scalars().all())

        topic_rows = (await session.execute(
            select(Topic.id, Topic.title)
        )).all()
        topics = {tid: title for tid, title in topic_rows}

        metrics = await _student_metrics(session, today)
        today_metrics = await _today_metrics(session, today)

        board = [
            _student_from_rows(user, topics, metrics, today_metrics)
            for user in users
        ]
        board.sort(key=lambda s: (
            -(
                s["debt"] * 3
                + s["pending"] * 2
                + max(0, s["today_total"] - s["today_completed"])
            ),
            -s["daily_xp"],
            -s["xp"],
        ))

        week_rows = (await session.execute(
            select(
                DailyAssignment.assigned_date,
                func.count(),
                func.sum(case(
                    (DailyAssignment.is_completed.is_(True), 1), else_=0,
                )),
                func.sum(case(
                    (DailyAssignment.is_correct.is_(True), 1), else_=0,
                )),
                func.sum(case(
                    (DailyAssignment.is_correct.is_(False), 1), else_=0,
                )),
            )
            .where(DailyAssignment.assigned_date >= week_start)
            .group_by(DailyAssignment.assigned_date)
        )).all()
        week_by_date = {row[0]: row for row in week_rows}
        week = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            row = week_by_date.get(day)
            week.append({
                "date": day.isoformat(),
                "assigned": int(row[1] or 0) if row else 0,
                "completed": int(row[2] or 0) if row else 0,
                "correct": int(row[3] or 0) if row else 0,
                "wrong": int(row[4] or 0) if row else 0,
            })

        wrong_expr = func.sum(case(
            (DailyAssignment.is_correct.is_(False), 1), else_=0,
        ))
        correct_expr = func.sum(case(
            (DailyAssignment.is_correct.is_(True), 1), else_=0,
        ))
        weak_rows = (await session.execute(
            select(Topic.id, Topic.title, wrong_expr, correct_expr)
            .join(Task, Task.topic_id == Topic.id)
            .join(DailyAssignment, DailyAssignment.task_id == Task.id)
            .group_by(Topic.id)
            .having(wrong_expr > 0)
            .order_by(desc(wrong_expr))
            .limit(8)
        )).all()

        recent_rows = (await session.execute(
            select(DailyAssignment, User, Task)
            .join(User, User.id == DailyAssignment.user_id)
            .join(Task, Task.id == DailyAssignment.task_id)
            .where(DailyAssignment.is_completed.is_(True))
            .order_by(desc(DailyAssignment.answered_at))
            .limit(10)
        )).all()

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
        tasks_count = (await session.execute(
            select(func.count()).select_from(Task)
        )).scalar() or 0
        active_topic = (await session.execute(
            select(Topic).where(Topic.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()

    today_total = sum(s["today_total"] for s in board)
    today_completed = sum(s["today_completed"] for s in board)
    today_correct = sum(s["today_correct"] for s in board)
    today_wrong = sum(s["today_wrong"] for s in board)
    today_pending = sum(s["today_pending"] for s in board)
    debt_total = sum(s["debt"] for s in board)
    active_today = sum(1 for s in board if s["today_completed"] > 0)

    top_students = sorted(
        board, key=lambda s: (s["daily_xp"], s["xp"]), reverse=True,
    )[:5]

    return {
        "students": len(board),
        "topics": len(topics),
        "tasks": tasks_count,
        "pending_review": pending_review,
        "wrong_total": wrong_total,
        "today_total": today_total,
        "today_completed": today_completed,
        "today_correct": today_correct,
        "today_wrong": today_wrong,
        "today_pending": today_pending,
        "debt_total": debt_total,
        "active_today": active_today,
        "active_topic": (
            {"id": active_topic.id, "title": active_topic.title}
            if active_topic else None
        ),
        "board": board,
        "week": week,
        "weak_topics": [
            {
                "id": row[0],
                "title": row[1],
                "wrong": int(row[2] or 0),
                "correct": int(row[3] or 0),
            }
            for row in weak_rows
        ],
        "top_students": [
            {
                "id": s["id"],
                "username": s["username"],
                "level": s["level"],
                "rank": s["rank"],
                "xp": s["xp"],
                "daily_xp": s["daily_xp"],
                "daily_coins": s["daily_coins"],
                "coins": s["coins"],
                "streak": s["streak"],
                "today_completed": s["today_completed"],
                "today_total": s["today_total"],
            }
            for s in top_students
        ],
        "recent": [
            {
                "id": assignment.id,
                "student_id": user.id,
                "username": user.username,
                "task_text": task.text,
                "status": _status(assignment),
                "is_correct": assignment.is_correct,
                "answered_at": (
                    assignment.answered_at.isoformat()
                    if assignment.answered_at else None
                ),
            }
            for assignment, user, task in recent_rows
        ],
    }


# ─────────────────────────── Ученики ───────────────────────────

@router.get("/students")
async def students(
    query: str = "",
    flt: Literal[
        "all", "debt", "pending", "today_open", "active_today", "inactive",
    ] = Query("all", alias="filter"),
    sort_by: Literal[
        "xp", "debt", "pending", "level", "name", "today",
    ] = Query("xp", alias="sort"),
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
        users = list((await session.execute(
            q.limit(1000)
        )).scalars().all())

        topic_rows = (await session.execute(
            select(Topic.id, Topic.title)
        )).all()
        topics = {tid: title for tid, title in topic_rows}
        metrics = await _student_metrics(session, today)
        today_metrics = await _today_metrics(session, today)

    result = [
        _student_from_rows(user, topics, metrics, today_metrics)
        for user in users
    ]

    if flt == "debt":
        result = [s for s in result if s["debt"] > 0]
    elif flt == "pending":
        result = [s for s in result if s["pending"] > 0]
    elif flt == "today_open":
        result = [
            s for s in result if s["today_total"] > s["today_completed"]
        ]
    elif flt == "active_today":
        result = [s for s in result if s["today_completed"] > 0]
    elif flt == "inactive":
        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=7)
        )
        result = [
            s for s in result
            if not s["last_activity"]
            or datetime.fromisoformat(s["last_activity"]) < cutoff
        ]

    if sort_by == "debt":
        result.sort(key=lambda s: (-s["debt"], -s["pending"], -s["xp"]))
    elif sort_by == "pending":
        result.sort(key=lambda s: (-s["pending"], -s["debt"], -s["xp"]))
    elif sort_by == "level":
        result.sort(key=lambda s: (-s["level"], -s["xp"]))
    elif sort_by == "name":
        result.sort(key=lambda s: (s["username"] or "").lower())
    elif sort_by == "today":
        result.sort(key=lambda s: (-s["today_completed"], -s["xp"]))
    else:
        result.sort(key=lambda s: (-s["xp"], -s["level"]))

    result = result[:limit]
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
        metrics = await _student_metrics(session, today)
        today_metrics = await _today_metrics(session, today)
        topics = {topic.id: topic.title} if topic else {}

        recent = (await session.execute(
            select(DailyAssignment, Task)
            .join(Task, Task.id == DailyAssignment.task_id)
            .where(DailyAssignment.user_id == student_id)
            .order_by(desc(DailyAssignment.id))
            .limit(15)
        )).all()

    return {
        "student": _student_from_rows(user, topics, metrics, today_metrics),
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


@router.post("/students/{student_id}/bonus")
async def manual_bonus(student_id: int, body: BonusIn, request: Request):
    if body.xp <= 0 and body.coins <= 0:
        raise HTTPException(
            status_code=400,
            detail="Укажи XP или монеты",
        )
    async with async_session() as session:
        user = await _get_student(session, student_id)
        result = await award_bonus(
            session, user, xp=body.xp, coins=body.coins,
        )
        await session.commit()

        message = (
            "🎁 <b>Учитель начислил тебе награду</b>\n"
            f"+{result['xp']} XP, +{result['coins']} 🪙"
        )
        if body.comment:
            message += f"\n\n{html.escape(body.comment.strip())}"
        if result["leveled_up"]:
            message += f"\n🎉 Новый уровень: <b>{result['new_level']}</b>!"

    notified = await _notify(request, user.id, message)
    return {
        "ok": True,
        "notified": notified,
        "xp": result["xp"],
        "coins": result["coins"],
        "leveled_up": result["leveled_up"],
        "new_level": result["new_level"],
    }


@router.post("/students/{student_id}/message")
async def message_student(student_id: int, body: MessageIn, request: Request):
    async with async_session() as session:
        user = await _get_student(session, student_id)

    text = (
        "✉️ <b>Сообщение от учителя</b>\n\n"
        f"{html.escape(body.text.strip())}"
    )
    notified = await _notify(request, user.id, text)
    return {"ok": True, "notified": notified}


@router.post("/broadcast")
async def broadcast(body: BroadcastIn, request: Request):
    today = local_today()
    text = (
        "📣 <b>Сообщение от учителя</b>\n\n"
        f"{html.escape(body.text.strip())}"
    )

    async with async_session() as session:
        q = select(User.id).where(User.role == "student")
        if ADMIN_IDS:
            q = q.where(User.id.notin_(ADMIN_IDS))
        all_ids = {row[0] for row in (await session.execute(q)).all()}

        target_ids = all_ids
        if body.audience == "debt":
            rows = (await session.execute(
                select(DailyAssignment.user_id).where(
                    DailyAssignment.is_completed.is_(False),
                    DailyAssignment.assigned_date < today,
                ).distinct()
            )).all()
            target_ids = all_ids & {row[0] for row in rows}
        elif body.audience == "pending":
            rows = (await session.execute(
                select(DailyAssignment.user_id).where(
                    DailyAssignment.is_completed.is_(True),
                    DailyAssignment.is_correct.is_(None),
                ).distinct()
            )).all()
            target_ids = all_ids & {row[0] for row in rows}
        elif body.audience == "today_open":
            rows = (await session.execute(
                select(DailyAssignment.user_id).where(
                    DailyAssignment.assigned_date == today,
                    DailyAssignment.is_completed.is_(False),
                ).distinct()
            )).all()
            target_ids = all_ids & {row[0] for row in rows}

    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return {
            "ok": True,
            "notified": False,
            "total": len(target_ids),
            "sent": 0,
            "errors": 0,
        }

    sent = 0
    errors = 0
    for user_id in sorted(target_ids):
        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
            sent += 1
        except Exception:
            logger.exception("Не удалось отправить рассылку %s", user_id)
            errors += 1
        await asyncio.sleep(0.05)

    return {
        "ok": True,
        "notified": True,
        "total": len(target_ids),
        "sent": sent,
        "errors": errors,
    }


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


@router.patch("/topics/{topic_id}/tasks/{task_id}")
async def update_task(topic_id: int, task_id: int, body: TaskIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст задания пустой")
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task or task.topic_id != topic_id:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        task.text = text
        task.answer = (body.answer or "").strip() or None
        task.difficulty = body.difficulty
        await session.commit()
        await session.refresh(task)
        return {"ok": True, "task": _task_payload(task)}


@router.delete("/topics/{topic_id}/tasks/{task_id}")
async def delete_task(topic_id: int, task_id: int):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task or task.topic_id != topic_id:
            raise HTTPException(status_code=404, detail="Задание не найдено")

        assigned = (await session.execute(
            select(func.count()).select_from(DailyAssignment).where(
                DailyAssignment.task_id == task_id
            )
        )).scalar() or 0
        completed = (await session.execute(
            select(func.count()).select_from(CompletedTask).where(
                CompletedTask.task_id == task_id
            )
        )).scalar() or 0
        if assigned or completed:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Задание уже выдавалось ученикам "
                    f"({assigned} выдач, {completed} решений). "
                    "Удалить нельзя, чтобы не потерять прогресс."
                ),
            )

        await session.delete(task)
        await session.commit()
        return {"ok": True, "task_id": task_id}


@router.delete("/topics/{topic_id}")
async def delete_topic(topic_id: int):
    async with async_session() as session:
        topic = await _get_topic(session, topic_id)

        tasks_count = (await session.execute(
            select(func.count()).select_from(Task).where(
                Task.topic_id == topic_id
            )
        )).scalar() or 0
        users_count = (await session.execute(
            select(func.count()).select_from(User).where(
                User.current_topic_id == topic_id
            )
        )).scalar() or 0
        lessons_count = (await session.execute(
            select(func.count()).select_from(LessonSession).where(
                LessonSession.topic_id == topic_id
            )
        )).scalar() or 0
        boss_count = (await session.execute(
            select(func.count()).select_from(BossFight).where(
                BossFight.topic_id == topic_id
            )
        )).scalar() or 0
        progress_count = (await session.execute(
            select(func.count()).select_from(TopicProgress).where(
                TopicProgress.topic_id == topic_id
            )
        )).scalar() or 0
        required_count = (await session.execute(
            select(func.count()).select_from(Topic).where(
                Topic.required_topic_id == topic_id
            )
        )).scalar() or 0

        if tasks_count:
            raise HTTPException(
                status_code=409,
                detail=f"В теме ещё {tasks_count} заданий. Сначала удали их.",
            )
        if users_count or lessons_count or boss_count or progress_count:
            raise HTTPException(
                status_code=409,
                detail="Тема уже использовалась учениками, удалить нельзя.",
            )
        if required_count:
            raise HTTPException(
                status_code=409,
                detail="На эту тему ссылаются другие темы.",
            )

        await session.delete(topic)
        await session.commit()
        return {"ok": True, "topic_id": topic_id}


# ─────────────────────────── Проверка ответов ───────────────────────────

@router.get("/reviews")
async def reviews(
    kind: Literal["all", "pending", "wrong"] = "pending",
    student_id: int | None = None,
    page: int = Query(0, ge=0),
    per_page: int = Query(30, ge=1, le=200),
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

        total = (await session.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        rows = (await session.execute(
            q.order_by(desc(DailyAssignment.answered_at))
            .offset(page * per_page)
            .limit(per_page)
        )).all()
    return {
        "items": [
            _review_payload(assignment, user, task)
            for assignment, user, task in rows
        ],
        "total": total,
        "page": page,
        "pages": pages,
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


# ─────────────────────────── Экспорт CSV ───────────────────────────

def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def _csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    # BOM, чтобы Excel в Windows правильно открыл кириллицу.
    content = "\ufeff" + buffer.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/export/students.csv")
async def export_students():
    today = local_today()
    async with async_session() as session:
        q = select(User).where(User.role == "student")
        if ADMIN_IDS:
            q = q.where(User.id.notin_(ADMIN_IDS))
        users = list((await session.execute(
            q.order_by(User.username)
        )).scalars().all())

        topic_rows = (await session.execute(
            select(Topic.id, Topic.title)
        )).all()
        topics = {tid: title for tid, title in topic_rows}
        metrics = await _student_metrics(session, today)
        today_metrics = await _today_metrics(session, today)

    students_rows = [
        _student_from_rows(user, topics, metrics, today_metrics)
        for user in users
    ]

    header = [
        "id", "username", "level", "rank", "xp", "coins", "streak",
        "topic", "difficulty", "correct", "wrong", "pending", "debt",
        "today_total", "today_completed", "last_activity",
    ]
    rows = [
        [
            s["id"],
            _csv_safe(s["username"]),
            s["level"],
            _csv_safe(s["rank"]),
            s["xp"],
            s["coins"],
            s["streak"],
            _csv_safe(s["topic_title"]),
            s["difficulty"],
            s["correct"],
            s["wrong"],
            s["pending"],
            s["debt"],
            s["today_total"],
            s["today_completed"],
            _csv_safe(s["last_activity"]),
        ]
        for s in students_rows
    ]
    return _csv_response("students.csv", header, rows)


@router.get("/export/reviews.csv")
async def export_reviews(
    kind: Literal["all", "pending", "wrong"] = "all",
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
        rows = (await session.execute(
            q.order_by(desc(DailyAssignment.answered_at)).limit(5000)
        )).all()

    header = [
        "id", "student_id", "username", "assigned_date", "answered_at",
        "status", "difficulty", "task", "student_answer", "correct_answer",
    ]
    data = [
        [
            assignment.id,
            user.id,
            _csv_safe(user.username),
            assignment.assigned_date.isoformat(),
            _csv_safe(
                assignment.answered_at.isoformat()
                if assignment.answered_at else None
            ),
            _status(assignment),
            task.difficulty,
            _csv_safe(task.text),
            _csv_safe(assignment.student_answer),
            _csv_safe(task.answer),
        ]
        for assignment, user, task in rows
    ]
    return _csv_response("reviews.csv", header, data)
