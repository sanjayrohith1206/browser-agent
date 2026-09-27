"""Alembic environment.

The app runs migrations over its own connection (app.db.engine.migrate);
the alembic CLI connects using DATABASE_URL.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import Connection

from app.config import get_settings
from app.db.engine import create_engine
from app.db.schema import metadata

config = context.config
target_metadata = metadata


def run_with(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_from_settings() -> None:
    engine = create_engine(get_settings().resolved_database_url)
    async with engine.connect() as conn:
        await conn.run_sync(run_with)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().resolved_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    provided = config.attributes.get("connection")
    if provided is not None:
        run_with(provided)
    else:
        asyncio.run(run_from_settings())
