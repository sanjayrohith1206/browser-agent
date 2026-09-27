"""REST endpoints: health, tasks and task history."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.agent.events import Event
from app.api.deps import CurrentUser, ServicesDep, TaskServiceDep
from app.tasks.models import CreateTaskRequest, Task, TaskSummary
from app.tasks.service import TaskStillRunningError, TooManyTasksError
from app.tasks.store import TaskNotFoundError

router = APIRouter()


class TaskList(BaseModel):
    tasks: list[TaskSummary]


class EventList(BaseModel):
    events: list[Event]


class Health(BaseModel):
    status: str
    llm_provider: str | None
    llm_model: str | None
    detail: str | None = None


class Readiness(BaseModel):
    database: bool
    kv: bool


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")


@router.get("/healthz", response_model=Health)
async def health(services: ServicesDep) -> Health:
    settings = services.settings
    if services.tasks is None:
        return Health(
            status="degraded",
            llm_provider=None,
            llm_model=None,
            detail=services.unavailable_reason,
        )
    return Health(status="ok", llm_provider=settings.llm_provider, llm_model=settings.model_name)


@router.get("/readyz", response_model=Readiness)
async def ready(services: ServicesDep, response: Response) -> Readiness:
    readiness = Readiness(database=await services.db.ping(), kv=await services.kv.ping())
    if not (readiness.database and readiness.kv):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return readiness


@router.post("/api/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: CreateTaskRequest, user: CurrentUser, services: ServicesDep, tasks: TaskServiceDep
) -> Task:
    await services.limiter.check(
        f"tasks:{user.id}", services.settings.rate_limit_tasks_per_minute, 60
    )
    try:
        return await tasks.create(user.id, body)
    except TooManyTasksError:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many tasks are running. Wait for one to finish, or stop one.",
        ) from None


@router.get("/api/tasks", response_model=TaskList)
async def list_tasks(
    user: CurrentUser,
    services: ServicesDep,
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = None,
) -> TaskList:
    return TaskList(tasks=await services.db.tasks.recent(user.id, limit, before))


@router.get("/api/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str, user: CurrentUser, services: ServicesDep) -> Task:
    try:
        return await services.db.tasks.get(task_id, user.id)
    except TaskNotFoundError:
        raise _not_found() from None


@router.get("/api/tasks/{task_id}/events", response_model=EventList)
async def task_events(
    task_id: str,
    user: CurrentUser,
    services: ServicesDep,
    after_seq: int = Query(0, ge=0),
) -> EventList:
    """A task's stored events, to replay it from history."""
    try:
        await services.db.tasks.get(task_id, user.id)
        return EventList(events=await services.db.tasks.events(task_id, after_seq))
    except TaskNotFoundError:
        raise _not_found() from None


@router.post("/api/tasks/{task_id}/stop", response_model=Task)
async def stop_task(task_id: str, user: CurrentUser, tasks: TaskServiceDep) -> Task:
    try:
        return await tasks.stop(task_id, user.id)
    except TaskNotFoundError:
        raise _not_found() from None


@router.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, user: CurrentUser, services: ServicesDep) -> None:
    try:
        if services.tasks is not None:
            await services.tasks.delete(task_id, user.id)
        else:
            await services.db.tasks.delete(task_id, user.id)
    except TaskNotFoundError:
        raise _not_found() from None
    except TaskStillRunningError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Stop the task before deleting it."
        ) from None
