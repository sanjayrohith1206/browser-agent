from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import LOOPBACK_HOSTS, Settings
from app.db.crypto import Cipher, generate_key
from app.db.database import Database
from app.tools.registry import ToolRegistry


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_api_key=None,
        agent_max_steps=5,
        tool_timeout_seconds=2.0,
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        data_encryption_keys=generate_key(),
        allowed_hosts=[*LOOPBACK_HOSTS, "testserver"],
    )


@pytest.fixture
def registry(settings: Settings) -> ToolRegistry:
    return ToolRegistry.from_file(settings.shared_tools_path)


@dataclass
class DbFixture:
    database: Database
    user_id: str


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DbFixture]:
    """A migrated database with one user. SQLite by default; set
    TEST_DATABASE_URL (postgresql+asyncpg://...) to run on PostgreSQL, with
    a fresh database per test."""
    admin_url = os.environ.get("TEST_DATABASE_URL")
    if admin_url:
        name = f"test_{uuid.uuid4().hex[:12]}"
        await _admin(admin_url, f'CREATE DATABASE "{name}"')
        url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    database = Database(url, Cipher([generate_key()]))
    await database.migrate()
    user = await database.users.create("owner@example.com", "not-a-real-hash", "Owner")
    yield DbFixture(database, user.id)
    await database.close()
    if admin_url:
        await _admin(admin_url, f'DROP DATABASE "{name}"')


async def _admin(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(statement))
    await engine.dispose()
