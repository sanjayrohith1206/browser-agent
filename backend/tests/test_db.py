"""Database: migrations, encryption at rest, and the stores."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import Connection, inspect, select, text

from app.agent.events import Event, utcnow
from app.db import schema as t
from app.db.crypto import Cipher, EncryptionConfigError, generate_key, load_or_create_key_file
from app.db.database import Database
from app.db.engine import migrate
from app.db.memory import MAX_MEMORY_ITEMS, MemoryFullError, MemoryNotFoundError
from app.db.users import EmailTakenError
from app.tasks.models import PageContext, TabInfo, Task, TaskStep
from app.tasks.store import TaskNotFoundError
from app.tools.types import ToolError
from tests.conftest import DbFixture

SECRET = "my secret goal about Alice's surprise party"


async def test_migrations_match_the_schema(db: DbFixture) -> None:
    def diff(conn: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(conn), t.metadata))

    async with db.database.engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_downgrade_and_upgrade(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", Cipher([generate_key()]))
    await database.migrate()

    def tables(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    async with database.engine.connect() as conn:
        assert {"users", "tasks", "task_steps", "agent_messages", "memory"} <= await conn.run_sync(
            tables
        )
    from alembic import command

    from app.db.engine import alembic_config

    def downgrade(conn: Connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = conn
        command.downgrade(config, "base")

    async with database.engine.begin() as conn:
        await conn.run_sync(downgrade)
    async with database.engine.connect() as conn:
        assert await conn.run_sync(tables) == {"alembic_version"}
    await migrate(database.engine)
    async with database.engine.connect() as conn:
        assert "browser_sessions" in await conn.run_sync(tables)
    await database.close()


async def test_sensitive_data_is_encrypted_at_rest(db: DbFixture) -> None:
    store = db.database.tasks
    task = Task(
        goal=SECRET,
        page=PageContext(tab_id=1, url="https://private.example/inbox", title="Inbox"),
        current_url="https://private.example/inbox",
    )
    await store.create(task, db.user_id)
    await store.add_step(
        task.id, TaskStep(call_id="c1", tool="type_text", input={"text": "hunter2-ish"})
    )
    await store.finish_step(task.id, "c1", success=True, message="Typed “hunter2-ish”", error=None)
    await store.append_event(Event(type="agent_message", task_id=task.id, message=SECRET))
    await store.save_messages(task.id, 0, [HumanMessage(SECRET)])
    await db.database.memory.add(db.user_id, "My passport number is X1234567")

    async with db.database.engine.connect() as conn:
        dump = []
        for table in ("tasks", "task_steps", "task_events", "agent_messages", "memory"):
            rows = (await conn.execute(text(f"SELECT * FROM {table}"))).all()
            dump.append(repr(rows))
    raw = "\n".join(dump)
    for plaintext in ("surprise party", "private.example", "hunter2", "X1234567"):
        assert plaintext not in raw

    # ...and reads back intact.
    loaded = await store.get(task.id, db.user_id)
    assert loaded.goal == SECRET and loaded.page and loaded.page.url.endswith("/inbox")
    assert loaded.steps[0].input == {"text": "hunter2-ish"}


async def test_wrong_key_cannot_read(db: DbFixture) -> None:
    task = await db.database.tasks.create(Task(goal=SECRET), db.user_id)
    other = Database(db.database.url, Cipher([generate_key()]))
    with pytest.raises(EncryptionConfigError):
        await other.tasks.get(task.id)
    await other.close()


async def test_key_rotation_reads_old_data(db: DbFixture) -> None:
    old_key, new_key = generate_key(), generate_key()
    before = Database(db.database.url, Cipher([old_key]))
    task = await before.tasks.create(Task(goal=SECRET), db.user_id)
    # The new key encrypts; the old one, listed after it, still decrypts.
    after = Database(db.database.url, Cipher([new_key, old_key]))
    assert (await after.tasks.get(task.id)).goal == SECRET
    await before.close()
    await after.close()


def test_key_file_is_created_private(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "encryption.key"
    key = load_or_create_key_file(path)
    assert load_or_create_key_file(path) == key
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    Cipher([key])  # valid


async def test_task_store_round_trip(db: DbFixture) -> None:
    store = db.database.tasks
    task = await store.create(Task(goal="Compare laptops"), db.user_id)
    await store.update(
        task.id,
        status="completed",
        result="The first one.",
        outcome="done",
        current_url="https://shop.example/a",
        current_tab=9,
        tabs=[TabInfo(tab_id=9, url="https://shop.example/a", opened_by_agent=True)],
    )
    await store.add_step(task.id, TaskStep(call_id="c1", tool="click_element", input={}))
    await store.finish_step(
        task.id,
        "c1",
        success=False,
        message="That part of the page is gone",
        error=ToolError(code="ELEMENT_NOT_FOUND", message="gone"),
    )
    loaded = await store.get(task.id, db.user_id)
    assert (loaded.status, loaded.result, loaded.outcome) == ("completed", "The first one.", "done")
    assert loaded.tabs == [TabInfo(tab_id=9, url="https://shop.example/a", opened_by_agent=True)]
    assert loaded.steps[0].error == ToolError(code="ELEMENT_NOT_FOUND", message="gone")
    assert loaded.created_at.tzinfo is not None

    with pytest.raises(TaskNotFoundError):
        await store.get(task.id, "someone-else")
    with pytest.raises(ValueError):
        await store.update(task.id, goal="rewritten")


async def test_history_is_newest_first_and_paged(db: DbFixture) -> None:
    store = db.database.tasks
    now = utcnow()
    for i in range(5):
        await store.create(
            Task(goal=f"task {i}", created_at=now + timedelta(seconds=i)), db.user_id
        )
    page1 = await store.recent(db.user_id, limit=2)
    assert [s.goal for s in page1] == ["task 4", "task 3"]
    page2 = await store.recent(db.user_id, limit=2, before=page1[-1].created_at)
    assert [s.goal for s in page2] == ["task 2", "task 1"]


async def test_events_are_numbered_and_fragments_not_stored(db: DbFixture) -> None:
    store = db.database.tasks
    task = await store.create(Task(goal="x"), db.user_id)
    seqs = [
        (await store.append_event(Event(type=kind, task_id=task.id, message="m"))).seq
        for kind in ("task_status", "agent_message_delta", "agent_message")
    ]
    assert seqs == [1, 2, 3]
    assert [e.seq for e in await store.events(task.id)] == [1, 3]
    assert [e.seq for e in await store.events(task.id, after_seq=1)] == [3]
    # Numbering continues from storage after the cache is dropped.
    store.forget(task.id)
    assert (await store.append_event(Event(type="error", task_id=task.id))).seq == 4
    with pytest.raises(TaskNotFoundError):
        await store.append_event(Event(type="error", task_id="missing"))


async def test_conversation_is_stored_without_screenshots(db: DbFixture) -> None:
    store = db.database.tasks
    task = await store.create(Task(goal="x"), db.user_id)
    screenshot = HumanMessage(
        content=[
            {"type": "text", "text": "Screenshot:"},
            {"type": "image", "base64": "QUJD", "mime_type": "image/jpeg"},
        ]
    )
    ai = AIMessage(content="", tool_calls=[{"id": "t1", "name": "take_screenshot", "args": {}}])
    await store.save_messages(task.id, 0, [HumanMessage("hi"), ai])
    await store.save_messages(
        task.id, 2, [ToolMessage(content="{}", tool_call_id="t1"), screenshot]
    )
    messages = await store.messages(task.id)
    assert [m.type for m in messages] == ["human", "ai", "tool", "human"]
    assert messages[1].tool_calls[0]["name"] == "take_screenshot"  # type: ignore[attr-defined]
    assert "QUJD" not in str(messages[3].content)


async def test_restart_interrupts_unfinished_and_retention(db: DbFixture) -> None:
    store = db.database.tasks
    running = await store.create(Task(goal="a", status="running"), db.user_id)
    waiting = await store.create(Task(goal="b", status="waiting"), db.user_id)
    old = await store.create(
        Task(goal="c", status="completed", created_at=utcnow() - timedelta(days=40)), db.user_id
    )
    assert await store.interrupt_unfinished() == 2
    assert (await store.get(running.id)).status == "interrupted"
    assert (await store.get(waiting.id)).error == "The server restarted before the task finished."
    assert await store.purge_before(utcnow() - timedelta(days=30)) == 1
    with pytest.raises(TaskNotFoundError):
        await store.get(old.id)


async def test_deleting_a_task_removes_its_rows(db: DbFixture) -> None:
    store = db.database.tasks
    task = await store.create(Task(goal="x"), db.user_id)
    await store.add_step(task.id, TaskStep(call_id="c1", tool="wait", input={}))
    await store.append_event(Event(type="task_status", task_id=task.id, status="queued"))
    await db.database.browser_sessions.opened(task.id, db.user_id, "test")
    await store.delete(task.id, db.user_id)
    async with db.database.engine.connect() as conn:
        for table in (t.task_steps, t.task_events, t.browser_sessions):
            assert (await conn.execute(select(table))).all() == []


async def test_memory(db: DbFixture) -> None:
    memory = db.database.memory
    item = await memory.add(db.user_id, "I prefer aisle seats")
    await memory.add(db.user_id, "My size is M")
    updated = await memory.update(db.user_id, item.id, "I prefer window seats")
    assert updated.content == "I prefer window seats"
    assert [m.content for m in await memory.list(db.user_id)] == [
        "I prefer window seats",
        "My size is M",
    ]
    other = await db.database.users.create("other@example.com", "x")
    assert await memory.list(other.id) == []
    with pytest.raises(MemoryNotFoundError):
        await memory.delete(other.id, item.id)
    await memory.delete(db.user_id, item.id)
    assert len(await memory.list(db.user_id)) == 1

    for i in range(MAX_MEMORY_ITEMS - 1):
        await memory.add(db.user_id, f"note {i}")
    with pytest.raises(MemoryFullError):
        await memory.add(db.user_id, "one too many")


async def test_users(db: DbFixture) -> None:
    users = db.database.users
    with pytest.raises(EmailTakenError):
        await users.create("  OWNER@example.com ", "x")
    found = await users.credentials("Owner@Example.com")
    assert found is not None and found[0].id == db.user_id
    assert await users.count() == 1
