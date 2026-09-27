"""Database schema (SQLAlchemy Core). Changes need an Alembic migration in
backend/migrations/versions.

Columns ending in _enc hold Fernet ciphertext (see app.db.crypto).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING)


def _ts(name: str, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("display_name", String(100), nullable=False, server_default=""),
    Column("disabled", Boolean, nullable=False, server_default=false()),
    _ts("created_at"),
    _ts("last_login_at", nullable=True),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("status", String(20), nullable=False),
    Column("outcome", String(20)),
    Column("goal_enc", Text, nullable=False),
    Column("result_enc", Text),
    # Errors are messages the server writes itself; they hold no user data.
    Column("error", Text),
    Column("page_enc", Text),
    Column("current_url_enc", Text),
    Column("current_tab", Integer),
    # The tabs the task has worked with (JSON list of TabInfo).
    Column("tabs_enc", Text),
    _ts("created_at"),
    _ts("updated_at"),
    Index("ix_tasks_user_created", "user_id", "created_at"),
    Index("ix_tasks_status", "status"),
)

task_steps = Table(
    "task_steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("call_id", String(64), nullable=False),
    Column("tool", String(64), nullable=False),
    Column("input_enc", Text, nullable=False),
    Column("tab_id", Integer),
    Column("success", Boolean),
    Column("message_enc", Text),
    Column("error_code", String(40)),
    Column("error_message_enc", Text),
    _ts("started_at"),
    _ts("finished_at", nullable=True),
    UniqueConstraint("task_id", "call_id", name="uq_task_steps_task_call"),
    Index("ix_task_steps_task", "task_id"),
)

task_events = Table(
    "task_events",
    metadata,
    Column(
        "task_id",
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("type", String(40), nullable=False),
    Column("payload_enc", Text, nullable=False),
    _ts("created_at"),
)

agent_messages = Table(
    "agent_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("role", String(20), nullable=False),
    Column("content_enc", Text, nullable=False),
    _ts("created_at"),
    UniqueConstraint("task_id", "position", name="uq_agent_messages_task_position"),
)

memory = Table(
    "memory",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("content_enc", Text, nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
    Index("ix_memory_user", "user_id"),
)

browser_sessions = Table(
    "browser_sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("client", String(200), nullable=False, server_default=""),
    _ts("connected_at"),
    _ts("disconnected_at", nullable=True),
    Column("close_reason", String(40)),
    Index("ix_browser_sessions_task", "task_id"),
)
