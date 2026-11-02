from datetime import datetime, date

from sqlalchemy import (
    BigInteger, String, Integer, ForeignKey, Boolean, Date, DateTime, func, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="student")
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_completed_date: Mapped[date | None] = mapped_column(Date)
    current_topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"))
    current_difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # RPG-слой
    coins: Mapped[int] = mapped_column(Integer, default=0)
    daily_xp: Mapped[int] = mapped_column(Integer, default=0)
    daily_coins: Mapped[int] = mapped_column(Integer, default=0)
    daily_reset_date: Mapped[date | None] = mapped_column(Date)

    current_topic: Mapped["Topic | None"] = relationship(foreign_keys=[current_topic_id])
    completed_tasks: Mapped[list["CompletedTask"]] = relationship(back_populates="user")
    achievements: Mapped[list["UserAchievement"]] = relationship(back_populates="user")
    daily_assignments: Mapped[list["DailyAssignment"]] = relationship(back_populates="user")
    lessons: Mapped[list["LessonSession"]] = relationship(back_populates="user")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # RPG-слой
    lore: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji: Mapped[str] = mapped_column(String(16), default="📚")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    required_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id"), nullable=True
    )

    tasks: Mapped[list["Task"]] = relationship(back_populates="topic")
    lessons: Mapped[list["LessonSession"]] = relationship(back_populates="topic")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)   # эталонный ответ
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # RPG-слой
    skill: Mapped[str] = mapped_column(String(20), default="algebra")
    hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_boss: Mapped[bool] = mapped_column(Boolean, default=False)

    topic: Mapped["Topic"] = relationship(back_populates="tasks")
    target_user: Mapped["User | None"] = relationship(foreign_keys=[target_user_id])
    daily_assignments: Mapped[list["DailyAssignment"]] = relationship(back_populates="task")
    completed_tasks: Mapped[list["CompletedTask"]] = relationship(back_populates="task")


class DailyAssignment(Base):
    __tablename__ = "daily_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "task_id", "assigned_date",
            name="uq_daily_user_task_date",
        ),
        Index("ix_daily_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    assigned_date: Mapped[date] = mapped_column(Date, default=date.today)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    student_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)

    # RPG-слой
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_hint: Mapped[bool] = mapped_column(Boolean, default=False)
    manually_accepted: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="daily_assignments")
    task: Mapped["Task"] = relationship(back_populates="daily_assignments")


class CompletedTask(Base):
    __tablename__ = "completed_tasks"
    __table_args__ = (
        Index("ix_completed_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    completed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)

    # RPG-слой
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    used_hint: Mapped[bool] = mapped_column(Boolean, default=False)
    first_clear: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="daily")
    skill: Mapped[str | None] = mapped_column(String(20), nullable=True)
    coins_earned: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="completed_tasks")
    task: Mapped["Task"] = relationship(back_populates="completed_tasks")


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500))
    xp_bonus: Mapped[int] = mapped_column(Integer, default=0)

    user_achievements: Mapped[list["UserAchievement"]] = relationship(back_populates="achievement")


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievements.id"))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="achievements")
    achievement: Mapped["Achievement"] = relationship(back_populates="user_achievements")


class LessonSession(Base):
    __tablename__ = "lesson_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    difficulty: Mapped[str] = mapped_column(String(20))
    conducted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="lessons")
    topic: Mapped["Topic"] = relationship(back_populates="lessons")


# ─────────────────────────── RPG ───────────────────────────


class SkillProgress(Base):
    __tablename__ = "skill_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "skill", name="uq_skill_user_skill"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    skill: Mapped[str] = mapped_column(String(20))
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TopicProgress(Base):
    __tablename__ = "topic_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "topic_id", name="uq_topic_user_topic"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    mastery: Mapped[int] = mapped_column(Integer, default=0)   # 0..100
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Quest(Base):
    __tablename__ = "quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500))
    type: Mapped[str] = mapped_column(String(20), default="daily")
    goal_type: Mapped[str] = mapped_column(String(30), default="solve_tasks")
    goal_value: Mapped[int] = mapped_column(Integer, default=3)
    reward_xp: Mapped[int] = mapped_column(Integer, default=0)
    reward_coins: Mapped[int] = mapped_column(Integer, default=0)
    reward_item_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserQuest(Base):
    __tablename__ = "user_quests"
    __table_args__ = (
        UniqueConstraint("user_id", "quest_id", "period_key", name="uq_user_quest_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    quest_id: Mapped[int] = mapped_column(ForeignKey("quests.id"))
    period_key: Mapped[str] = mapped_column(String(20))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500))
    emoji: Mapped[str] = mapped_column(String(16), default="🎁")
    price: Mapped[int] = mapped_column(Integer, default=0)
    effect: Mapped[str] = mapped_column(String(30), default="cosmetic")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", name="uq_inventory_user_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)


class BossFight(Base):
    __tablename__ = "boss_fights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    hp: Mapped[int] = mapped_column(Integer, default=5)
    hearts: Mapped[int] = mapped_column(Integer, default=3)
    task_ids: Mapped[str] = mapped_column(Text)          # "1,2,3,4,5,6,7"
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
