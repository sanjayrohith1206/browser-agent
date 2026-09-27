"""User accounts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent.events import utcnow
from app.db import schema as t


class User(BaseModel):
    id: str
    email: str
    display_name: str
    disabled: bool = False
    created_at: datetime
    last_login_at: datetime | None = None


class EmailTakenError(ValueError):
    pass


class UserStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, email: str, password_hash: str, display_name: str = "") -> User:
        user = User(
            id=str(uuid.uuid4()),
            email=normalize_email(email),
            display_name=display_name.strip(),
            created_at=utcnow(),
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    insert(t.users).values(
                        id=user.id,
                        email=user.email,
                        password_hash=password_hash,
                        display_name=user.display_name,
                        disabled=False,
                        created_at=user.created_at,
                    )
                )
        except IntegrityError:
            raise EmailTakenError(user.email) from None
        return user

    async def get(self, user_id: str) -> User | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(t.users).where(t.users.c.id == user_id))).first()
        return _user(row) if row else None

    async def credentials(self, email: str) -> tuple[User, str] | None:
        """The user and their password hash, by email."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(select(t.users).where(t.users.c.email == normalize_email(email)))
            ).first()
        return (_user(row), row.password_hash) if row else None

    async def count(self) -> int:
        async with self._engine.connect() as conn:
            return int(await conn.scalar(select(func.count()).select_from(t.users)) or 0)

    async def touch_login(self, user_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(t.users).where(t.users.c.id == user_id).values(last_login_at=utcnow())
            )

    async def set_password(self, user_id: str, password_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(t.users).where(t.users.c.id == user_id).values(password_hash=password_hash)
            )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _user(row: Row[Any]) -> User:
    def utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    created = utc(row.created_at)
    assert created is not None
    return User(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        disabled=bool(row.disabled),
        created_at=created,
        last_login_at=utc(row.last_login_at),
    )
