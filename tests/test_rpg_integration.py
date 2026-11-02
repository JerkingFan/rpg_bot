"""Интеграционный тест RPG-петли на отдельной in-memory SQLite."""
import unittest
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, DailyAssignment, Inventory, Item, Task, Topic, User
from services.rpg import (
    answer_boss,
    boss_current_task,
    complete_task,
    ensure_rpg_defaults,
    get_daily_quest,
    register_wrong_attempt,
    start_boss,
)


class RpgLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.session_factory() as s:
            await ensure_rpg_defaults(s)
            user = User(id=1, username="tester", role="student")
            topic = Topic(title="Алгебра. Тест", is_active=True, order_index=1)
            s.add_all([user, topic])
            await s.flush()
            user.current_topic_id = topic.id
            user.current_difficulty = "easy"
            self.tasks = [
                Task(topic_id=topic.id, text=f"1+{i}", answer=str(1 + i), difficulty="easy", skill="algebra")
                for i in range(1, 5)
            ]
            s.add_all(self.tasks)
            await s.commit()
            self.user_id = user.id
            self.topic_id = topic.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _complete(self, task, day_offset):
        async with self.session_factory() as s:
            user = await s.get(User, self.user_id)
            a = DailyAssignment(
                user_id=user.id,
                task_id=task.id,
                assigned_date=date.today() - timedelta(days=day_offset),
            )
            s.add(a)
            await s.commit()
            await s.refresh(a)
            result = await complete_task(s, user, task, a)
            await s.commit()
            return result

    async def test_three_tasks_complete_daily_quest(self):
        results = [await self._complete(t, i) for i, t in enumerate(self.tasks[:3])]
        self.assertIsNone(results[0]["quest_reward"])
        self.assertIsNone(results[1]["quest_reward"])
        self.assertIsNotNone(results[2]["quest_reward"])

        async with self.session_factory() as s:
            user = await s.get(User, self.user_id)
            quest, uq = await get_daily_quest(s, user)
            self.assertEqual(uq.progress, quest.goal_value)
            self.assertIsNotNone(uq.claimed_at)
            self.assertEqual(user.coins, 15 + 15)  # 3 задачи + квест
            # 30 XP за задачи + 20 за квест (+ возможные достижения).
            self.assertGreaterEqual(user.xp, 30 + 20)

    async def test_repeat_gives_no_xp(self):
        first = await self._complete(self.tasks[0], 0)
        repeat = await self._complete(self.tasks[0], 1)
        self.assertTrue(first["first_clear"])
        self.assertFalse(repeat["first_clear"])
        self.assertEqual(repeat["earned_xp"], 0)
        self.assertEqual(repeat["earned_coins"], 2)

    async def test_two_wrong_attempts_close_task(self):
        async with self.session_factory() as s:
            user = await s.get(User, self.user_id)
            task = self.tasks[0]
            a = DailyAssignment(user_id=user.id, task_id=task.id, assigned_date=date.today())
            s.add(a)
            await s.commit()
            await s.refresh(a)

            first = await register_wrong_attempt(s, user, a, task, "нет")
            second = await register_wrong_attempt(s, user, a, task, "нет")
            await s.commit()

            self.assertFalse(first["final"])
            self.assertTrue(second["final"])
            self.assertTrue(a.is_completed)
            self.assertFalse(a.is_correct)
            self.assertEqual(user.xp, 0)

    async def test_streak_freeze_saves_one_missed_day(self):
        async with self.session_factory() as s:
            user = await s.get(User, self.user_id)
            user.streak = 5
            user.last_completed_date = date.today() - timedelta(days=2)
            item = (await s.execute(
                select(Item).where(Item.code == "streak_freeze")
            )).scalar_one()
            s.add(Inventory(user_id=user.id, item_id=item.id, quantity=1))
            await s.commit()

        await self._complete(self.tasks[0], 0)

        async with self.session_factory() as s:
            user = await s.get(User, self.user_id)
            inv = (await s.execute(
                select(Inventory).where(Inventory.user_id == user.id)
            )).scalars().all()
            self.assertEqual(user.streak, 6)
            self.assertTrue(all(row.quantity == 0 for row in inv))

    async def test_first_boss_win_gives_full_reward(self):
        async with self.session_factory() as s:
            user = await s.get(User, self.user_id)
            started = await start_boss(s, user)
            await s.commit()
            self.assertTrue(started["ok"])
            fight = started["fight"]

            for _ in range(7):
                task = await boss_current_task(s, fight)
                if not task:
                    break
                result = await answer_boss(s, user, fight, task.answer or "")
                await s.commit()
                if result["status"] != "active":
                    break

            self.assertEqual(fight.status, "won")
            self.assertTrue(result["first_win"])
            self.assertEqual(result["reward_xp"], 100)
            self.assertEqual(result["reward_coins"], 50)

    async def test_second_boss_win_gives_smaller_reward(self):
        for expected_first in (True, False):
            async with self.session_factory() as s:
                user = await s.get(User, self.user_id)
                started = await start_boss(s, user)
                await s.commit()
                fight = started["fight"]
                for _ in range(7):
                    task = await boss_current_task(s, fight)
                    if not task:
                        break
                    result = await answer_boss(s, user, fight, task.answer or "")
                    await s.commit()
                    if result["status"] != "active":
                        break
                self.assertEqual(result["first_win"], expected_first)


if __name__ == "__main__":
    unittest.main()
