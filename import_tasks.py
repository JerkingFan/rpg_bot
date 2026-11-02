import asyncio
import sys
from database.base import init_db
from services.tasks_importer import import_from_file


async def main():
    if len(sys.argv) < 2:
        print("Использование: python import_tasks.py <file.txt>")
        return

    await init_db()

    path = sys.argv[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"❌ Файл не найден: {path}")
        return

    stats = await import_from_file(content)

    print(f"✅ Тем создано: {stats['topics_created']}")
    print(f"✅ Заданий добавлено: {stats['tasks_added']}")
    if stats["errors"]:
        print("⚠️ Ошибки:")
        for e in stats["errors"]:
            print(f"  • {e}")


if __name__ == "__main__":
    asyncio.run(main())
