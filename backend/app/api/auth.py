"""Accounts and sign-in.

The first account can always be created, so a fresh install is set up from
the extension; after that, registration needs ALLOW_REGISTRATION=true.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentUser, ServicesDep, bearer_token, client_ip
from app.auth.passwords import (
    DUMMY_HASH,
    MAX_PASSWORD_CHARS,
    hash_password,
    password_problem,
    verify_password,
)
from app.db.users import EmailTakenError, User, normalize_email
from app.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_WINDOW_SECONDS = 15 * 60
REGISTER_LOCK_OWNER = "register"


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = normalize_email(v)
        local, _, domain = v.partition("@")
        if not local or "." not in domain or " " in v:
            raise ValueError("enter a valid email address")
        return v


class Registration(Credentials):
    display_name: str = Field(default="", max_length=100)


class SignedIn(BaseModel):
    token: str
    user: User


class AuthStatus(BaseModel):
    registration_open: bool


async def registration_open(services: ServicesDep) -> bool:
    return services.settings.allow_registration or await services.db.users.count() == 0


@router.get("/status", response_model=AuthStatus)
async def auth_status(services: ServicesDep) -> AuthStatus:
    return AuthStatus(registration_open=await registration_open(services))


@router.post("/register", response_model=SignedIn, status_code=status.HTTP_201_CREATED)
async def register(body: Registration, request: Request, services: ServicesDep) -> SignedIn:
    await services.limiter.check(
        f"register:{client_ip(request)}",
        services.settings.rate_limit_logins_per_15m,
        LOGIN_WINDOW_SECONDS,
    )
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
    password_hash = await asyncio.to_thread(hash_password, body.password)

    # Serialize registrations so two "first accounts" can't race.
    kv = services.kv
    for _ in range(50):
        if await kv.acquire("lock:register", REGISTER_LOCK_OWNER, 10):
            break
        await asyncio.sleep(0.1)
    try:
        if not await registration_open(services):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="New accounts can't be created on this server. Ask its owner.",
            )
        try:
            user = await services.db.users.create(body.email, password_hash, body.display_name)
        except EmailTakenError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="An account with this email already exists."
            ) from None
    finally:
        await kv.release("lock:register", REGISTER_LOCK_OWNER)

    log.info("user_registered", user_id=user.id)
    token = await services.sessions.create(user.id)
    return SignedIn(token=token, user=user)


@router.post("/login", response_model=SignedIn)
async def login(body: Credentials, request: Request, services: ServicesDep) -> SignedIn:
    limits = services.settings.rate_limit_logins_per_15m
    await services.limiter.check(f"login-ip:{client_ip(request)}", limits, LOGIN_WINDOW_SECONDS)
    await services.limiter.check(f"login-email:{body.email}", limits, LOGIN_WINDOW_SECONDS)

    found = await services.db.users.credentials(body.email)
    stored = found[1] if found else DUMMY_HASH
    valid = await asyncio.to_thread(verify_password, body.password, stored)
    if found is None or not valid or found[0].disabled:
        log.info("login_failed", ip=client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Wrong email or password.")
    user = found[0]
    await services.db.users.touch_login(user.id)
    token = await services.sessions.create(user.id)
    log.info("login", user_id=user.id)
    return SignedIn(token=token, user=user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, services: ServicesDep, _user: CurrentUser) -> None:
    token = bearer_token(request)
    if token:
        await services.sessions.revoke(token)


@router.get("/me", response_model=User)
async def me(user: CurrentUser) -> User:
    return user
