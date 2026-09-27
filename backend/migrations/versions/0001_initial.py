"""Initial schema: users, tasks, task_steps, task_events, agent_messages,
memory and browser_sessions.

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("created_at"),
        _ts("last_login_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("goal_enc", sa.Text(), nullable=False),
        sa.Column("result_enc", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("page_enc", sa.Text(), nullable=True),
        sa.Column("current_url_enc", sa.Text(), nullable=True),
        sa.Column("current_tab", sa.Integer(), nullable=True),
        sa.Column("tabs_enc", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_tasks_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_tasks_user_created", "tasks", ["user_id", "created_at"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "task_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("input_enc", sa.Text(), nullable=False),
        sa.Column("tab_id", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("message_enc", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error_message_enc", sa.Text(), nullable=True),
        _ts("started_at"),
        _ts("finished_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_task_steps"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_task_steps_task_id_tasks", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("task_id", "call_id", name="uq_task_steps_task_call"),
    )
    op.create_index("ix_task_steps_task", "task_steps", ["task_id"])

    op.create_table(
        "task_events",
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("payload_enc", sa.Text(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("task_id", "seq", name="pk_task_events"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_task_events_task_id_tasks", ondelete="CASCADE"
        ),
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content_enc", sa.Text(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_messages"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_agent_messages_task_id_tasks",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_id", "position", name="uq_agent_messages_task_position"),
    )

    op.create_table(
        "memory",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("content_enc", sa.Text(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_memory"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memory_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_memory_user", "memory", ["user_id"])

    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("client", sa.String(200), nullable=False, server_default=""),
        _ts("connected_at"),
        _ts("disconnected_at", nullable=True),
        sa.Column("close_reason", sa.String(40), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_browser_sessions"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_browser_sessions_task_id_tasks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_browser_sessions_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_browser_sessions_task", "browser_sessions", ["task_id"])


def downgrade() -> None:
    op.drop_table("browser_sessions")
    op.drop_table("memory")
    op.drop_table("agent_messages")
    op.drop_table("task_events")
    op.drop_table("task_steps")
    op.drop_table("tasks")
    op.drop_table("users")
