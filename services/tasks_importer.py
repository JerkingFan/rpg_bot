import re
from sqlalchemy import select, func
from database.base import async_session
from database.models import User, Topic, Task
from services.rpg import skill_for_topic


def _parse_section_header(line: str) -> tuple[str, str | None]:
    """'[topic] Математика | medium' -> ('Математика', 'medium')"""
    body = line.split("]", 1)[1].strip()
    difficulty = None
    if "|" in body:
        name, diff = body.split("|", 1)
        return name.strip(), diff.strip().lower()
    return body, None


def parse_tasks_file(content: str) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    buf: list[str] = []
    buf_answer: str | None = None
    buf_difficulty: str | None = None

    def flush_task():
        nonlocal buf, buf_answer, buf_difficulty
        if current is None:
            buf, buf_answer, buf_difficulty = [], None, None
            return
        text = "\n".join(buf).strip()
        if text:
            current["tasks"].append({
                "text": text,
                "answer": buf_answer,
                "difficulty": buf_difficulty,
            })
        buf, buf_answer, buf_difficulty = [], None, None

    def flush_section():
        nonlocal current
        flush_task()
        if current is not None and current["tasks"]:
            sections.append(current)
        current = None

    for raw in content.split("\n"):
        line = raw.rstrip()

        if line.startswith("[topic]"):
            flush_section()
            name, diff = _parse_section_header(line)
            current = {"type": "topic", "name": name, "difficulty": diff or "medium", "tasks": []}
            continue

        if line.startswith("[individual]"):
            flush_section()
            body = line[len("[individual]"):].strip()
            topic_name = None
            if "|" in body:
                user_part, topic_name = body.split("|", 1)
                user_part = user_part.strip()
                topic_name = topic_name.strip()
            else:
                user_part = body
            username = user_id = None
            if user_part.startswith("@"):
                username = user_part[1:]
            elif user_part.startswith("id:"):
                try: user_id = int(user_part[3:])
                except ValueError: pass
            else:
                username = user_part
            current = {
                "type": "individual", "username": username, "user_id": user_id,
                "topic_name": topic_name, "difficulty": "medium", "tasks": [],
            }
            continue

        if line.strip() == "---":
            flush_task()
            continue

        if current is not None:
            stripped = line.strip()
            # построчная сложность: [easy] / [medium] / [hard]
            if stripped.lower() in ("[easy]", "[medium]", "[hard]"):
                buf_difficulty = stripped.strip("[]").lower()
                continue
            if stripped.startswith("="):
                buf_answer = stripped[1:].strip()
                continue
            buf.append(line)

    flush_section()
    return sections


async def import_from_file(content: str) -> dict:
    sections = parse_tasks_file(content)
    stats = {"topics_created": 0, "tasks_added": 0, "errors": []}

    async with async_session() as session:
        for sec in sections:
            if sec["type"] == "topic":
                r = await session.execute(select(Topic).where(Topic.title == sec["name"]))
                topic = r.scalar_one_or_none()
                if not topic:
                    topic = Topic(title=sec["name"], is_active=False)
                    session.add(topic)
                    await session.flush()
                    stats["topics_created"] += 1
                for item in sec["tasks"]:
                    session.add(Task(
                        topic_id=topic.id,
                        text=item["text"],
                        answer=item["answer"],
                        difficulty=item.get("difficulty") or sec["difficulty"],
                        target_user_id=None,
                        skill=skill_for_topic(topic.title),
                    ))
                    stats["tasks_added"] += 1

            elif sec["type"] == "individual":
                user = None
                if sec["user_id"]:
                    user = await session.get(User, sec["user_id"])
                elif sec["username"]:
                    r = await session.execute(select(User).where(User.username == sec["username"]))
                    user = r.scalar_one_or_none()
                if not user:
                    stats["errors"].append(f"Ученик не найден: @{sec['username'] or sec['user_id']}")
                    continue

                if sec["topic_name"]:
                    r = await session.execute(select(Topic).where(Topic.title == sec["topic_name"]))
                    topic = r.scalar_one_or_none()
                    if not topic:
                        topic = Topic(title=sec["topic_name"], is_active=False)
                        session.add(topic)
                        await session.flush()
                        stats["topics_created"] += 1
                else:
                    if not user.current_topic_id:
                        stats["errors"].append(f"@{user.username} без темы — укажи '| Тема'")
                        continue
                    topic = await session.get(Topic, user.current_topic_id)

                for item in sec["tasks"]:
                    session.add(Task(
                        topic_id=topic.id,
                        text=item["text"],
                        answer=item["answer"],
                        difficulty=item.get("difficulty") or sec["difficulty"],
                        target_user_id=user.id,
                        skill=skill_for_topic(topic.title),
                    ))
                    stats["tasks_added"] += 1

        await session.commit()
    return stats
