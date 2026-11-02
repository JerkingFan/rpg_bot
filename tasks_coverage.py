"""Показывает, сколько задач в каждой теме, и что осталось пустым."""
import asyncio
from sqlalchemy import select, func
from database.base import init_db, async_session
from database.models import Topic, Task


async def main():
    await init_db()
    async with async_session() as session:
        topics = list((await session.execute(
            select(Topic).order_by(Topic.title)
        )).scalars().all())

        rows: list[tuple[str, int]] = []
        for t in topics:
            n = (await session.execute(
                select(func.count()).select_from(Task).where(Task.topic_id == t.id)
            )).scalar() or 0
            rows.append((t.title, n))

    total = len(rows)
    ok = [r for r in rows if r[1] >= 5]
    low = [r for r in rows if 0 < r[1] < 5]
    empty = [r for r in rows if r[1] == 0]

    print(f"📊 Всего тем:        {total}")
    print(f"✅ 5+ задач:         {len(ok)}")
    print(f"⚠️  1-4 задачи:       {len(low)}")
    print(f"❌ пусто:            {len(empty)}")
    print()

    if low:
        print("⚠️  Мало задач:")
        for title, n in low:
            print(f"   {n:>2}  · {title}")
        print()

    if empty:
        print("❌ Без задач (первые 20):")
        for title, _ in empty[:20]:
            print(f"     · {title}")
        if len(empty) > 20:
            print(f"     ... и ещё {len(empty) - 20}")


if __name__ == "__main__":
    asyncio.run(main())
