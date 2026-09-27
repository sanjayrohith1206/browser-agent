"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.deps import Services
from app.api.memory import router as memory_router
from app.api.ratelimit import RateLimitedError, RateLimiter
from app.api.routes import router as api_router
from app.api.security import GuardMiddleware, OriginPolicy
from app.auth.sessions import SessionManager
from app.config import Settings, get_settings
from app.db.database import Database
from app.kv import KeyValue, MemoryKV, RedisKV
from app.llm.factory import ProviderConfigError, build_provider
from app.llm.provider import LLMProvider
from app.logging import configure_logging, get_logger
from app.tasks.service import TaskService
from app.tools.registry import ToolRegistry
from app.websocket.tasks import router as ws_router

log = get_logger(__name__)

RETENTION_INTERVAL_SECONDS = 24 * 3600


def create_app(
    settings: Settings | None = None,
    *,
    provider: LLMProvider | None = None,
    database: Database | None = None,
    kv: KeyValue | None = None,
) -> FastAPI:
    """Build the app. provider, database and kv can be injected (tests);
    otherwise they are built from settings."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registry = ToolRegistry.from_file(settings.shared_tools_path)
        db = database or Database.from_settings(settings)
        if settings.database_auto_migrate:
            await db.migrate()
        store = kv or (RedisKV.from_url(settings.redis_url) if settings.redis_url else MemoryKV())
        if not settings.redis_url and kv is None:
            log.warning("redis_not_configured", effect="sign-ins end when the server restarts")

        llm = provider
        unavailable: str | None = None
        if llm is None:
            try:
                llm = build_provider(settings)
            except ProviderConfigError as exc:
                unavailable = str(exc)
                log.warning("llm_not_configured", reason=unavailable)

        service = None
        if llm is not None:
            service = TaskService(
                db.tasks,
                llm,
                registry,
                settings,
                kv=store,
                memory=db.memory,
                session_log=db.browser_sessions,
            )
            await service.recover()
            log.info(
                "agent_ready",
                provider=llm.name,
                model=settings.model_name,
                tools=registry.names,
                environment=settings.environment,
            )

        app.state.services = Services(
            settings=settings,
            db=db,
            kv=store,
            sessions=SessionManager(store, settings.session_ttl_hours),
            limiter=RateLimiter(store),
            tasks=service,
            unavailable_reason=unavailable,
        )
        retention = (
            asyncio.create_task(_retention_loop(service), name="retention")
            if service is not None and settings.task_retention_days > 0
            else None
        )
        try:
            yield
        finally:
            if retention is not None:
                retention.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retention
            if service is not None:
                await service.shutdown()
            if kv is None:
                await store.close()
            if database is None:
                await db.close()

    docs = not settings.is_production
    app = FastAPI(
        title="Browser Agent",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(
        GuardMiddleware,
        policy=OriginPolicy(settings.allowed_extension_ids),
        allowed_hosts=settings.allowed_hosts,
    )

    @app.exception_handler(RateLimitedError)
    async def rate_limited(_request: Request, exc: RateLimitedError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Too many requests. Please wait a moment and try again."},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse({"detail": "Something went wrong on the server."}, status_code=500)

    app.include_router(api_router)
    app.include_router(auth_router)
    app.include_router(memory_router)
    app.include_router(ws_router)
    return app


async def _retention_loop(service: TaskService) -> None:
    while True:
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)
        try:
            await service.purge_expired()
        except Exception:
            log.exception("retention_failed")
