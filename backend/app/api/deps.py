"""Shared request dependencies: the app's services and the signed-in user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.api.ratelimit import RateLimiter
from app.auth.sessions import SessionManager
from app.config import Settings
from app.db.database import Database
from app.db.users import User
from app.kv import KeyValue
from app.tasks.service import TaskService


@dataclass
class Services:
    settings: Settings
    db: Database
    kv: KeyValue
    sessions: SessionManager
    limiter: RateLimiter
    tasks: TaskService | None
    unavailable_reason: str | None = None


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def authenticate(services: Services, token: str | None) -> User | None:
    if not token:
        return None
    user_id = await services.sessions.resolve(token)
    if user_id is None:
        return None
    user = await services.db.users.get(user_id)
    if user is None or user.disabled:
        return None
    return user


async def current_user(request: Request, services: ServicesDep) -> User:
    user = await authenticate(services, bearer_token(request))
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await services.limiter.check(
        f"requests:{user.id}", services.settings.rate_limit_requests_per_minute, 60
    )
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def task_service(services: ServicesDep) -> TaskService:
    if services.tasks is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=services.unavailable_reason or "The agent is not configured.",
        )
    return services.tasks


TaskServiceDep = Annotated[TaskService, Depends(task_service)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
