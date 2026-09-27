"""Database engine and migrations.

PostgreSQL is the production database (postgresql+asyncpg://...). A local
SQLite file (sqlite+aiosqlite://...) works for development with no setup.
The schema is managed by Alembic; the app applies pending migrations at
startup unless DATABASE_AUTO_MIGRATE is false.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # One writer at a time; wait for locks instead of failing.
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs.update(pool_size=10, max_overflow=10, pool_recycle=1800)
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return config


async def migrate(engine: AsyncEngine, revision: str = "head") -> None:
    """Upgrade the database to `revision` over the app's own engine."""

    def upgrade(connection: Connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    async with engine.begin() as conn:
        await conn.run_sync(upgrade)
