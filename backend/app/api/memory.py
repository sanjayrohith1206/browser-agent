"""The user's memory: notes the assistant keeps in mind across tasks."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentUser, ServicesDep
from app.db.memory import (
    MAX_MEMORY_CHARS,
    MAX_MEMORY_ITEMS,
    MemoryFullError,
    MemoryItem,
    MemoryNotFoundError,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS)

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("memory must not be blank")
        return v


class MemoryList(BaseModel):
    items: list[MemoryItem]
    max_items: int = MAX_MEMORY_ITEMS


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="memory item not found")


@router.get("", response_model=MemoryList)
async def list_memory(user: CurrentUser, services: ServicesDep) -> MemoryList:
    return MemoryList(items=await services.db.memory.list(user.id))


@router.post("", response_model=MemoryItem, status_code=status.HTTP_201_CREATED)
async def add_memory(body: MemoryInput, user: CurrentUser, services: ServicesDep) -> MemoryItem:
    try:
        return await services.db.memory.add(user.id, body.content)
    except MemoryFullError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Memory is full ({MAX_MEMORY_ITEMS} items). Delete some first.",
        ) from None


@router.patch("/{item_id}", response_model=MemoryItem)
async def update_memory(
    item_id: str, body: MemoryInput, user: CurrentUser, services: ServicesDep
) -> MemoryItem:
    try:
        return await services.db.memory.update(user.id, item_id, body.content)
    except MemoryNotFoundError:
        raise _missing() from None


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(item_id: str, user: CurrentUser, services: ServicesDep) -> None:
    try:
        await services.db.memory.delete(user.id, item_id)
    except MemoryNotFoundError:
        raise _missing() from None
