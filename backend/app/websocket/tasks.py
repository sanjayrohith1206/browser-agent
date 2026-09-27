"""WebSocket /ws/tasks/{task_id}: streams task events to the extension and
receives browser tool results, answers and approvals back.

Browsers can't set headers on a WebSocket, so the sign-in token travels as
a subprotocol: the client offers ["browser-agent.v1", "auth.<token>"] and
the server accepts "browser-agent.v1".
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.agent.events import Event
from app.agent.interaction import MAX_ANSWER_CHARS
from app.api.deps import Services, authenticate
from app.api.ratelimit import RateLimitedError
from app.logging import get_logger
from app.tasks.service import SessionAlreadyAttachedError
from app.tasks.store import TaskNotFoundError
from app.tools.types import ToolResult

log = get_logger(__name__)
router = APIRouter()

PROTOCOL = "browser-agent.v1"
AUTH_PREFIX = "auth."

CLOSE_UNAUTHORIZED = 4401
CLOSE_UNAVAILABLE = 4503
CLOSE_NOT_FOUND = 4404
CLOSE_CONFLICT = 4409
CLOSE_RATE_LIMITED = 4429

# Messages a session may send per minute (tool results, answers, approvals).
MAX_MESSAGES_PER_MINUTE = 600


class ToolResultMessage(BaseModel):
    type: Literal["tool_result"]
    call_id: str = Field(max_length=64)
    result: ToolResult


class UserReplyMessage(BaseModel):
    type: Literal["user_reply"]
    request_id: str = Field(max_length=64)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)


class ConfirmationReplyMessage(BaseModel):
    type: Literal["confirmation_reply"]
    request_id: str = Field(max_length=64)
    approved: bool


class StopMessage(BaseModel):
    type: Literal["stop"]


AnyClientMessage = ToolResultMessage | UserReplyMessage | ConfirmationReplyMessage | StopMessage
ClientMessageT = Annotated[AnyClientMessage, Field(discriminator="type")]
ClientMessage: TypeAdapter[AnyClientMessage] = TypeAdapter(ClientMessageT)


def _token(ws: WebSocket) -> str | None:
    for protocol in ws.scope.get("subprotocols") or []:
        if protocol.startswith(AUTH_PREFIX):
            return str(protocol[len(AUTH_PREFIX) :])
    return None


@router.websocket("/ws/tasks/{task_id}")
async def task_socket(
    ws: WebSocket, task_id: str, after_seq: Annotated[int, Query(ge=0)] = 0
) -> None:
    services: Services = ws.app.state.services
    offered = ws.scope.get("subprotocols") or []
    await ws.accept(subprotocol=PROTOCOL if PROTOCOL in offered else None)

    user = await authenticate(services, _token(ws))
    if user is None:
        await ws.close(CLOSE_UNAUTHORIZED, reason="please sign in")
        return
    service = services.tasks
    if service is None:
        await ws.close(CLOSE_UNAVAILABLE, reason="agent not configured")
        return

    send_lock = asyncio.Lock()

    async def send(event: Event) -> None:
        async with send_lock:
            await ws.send_json(event.wire())

    client = ws.headers.get("user-agent", "")
    try:
        await service.attach(task_id, user.id, send, after_seq, client=client)
    except TaskNotFoundError:
        await ws.close(CLOSE_NOT_FOUND, reason="task not found")
        return
    except SessionAlreadyAttachedError:
        await ws.close(CLOSE_CONFLICT, reason="task already has a browser session")
        return

    log.info("session_attached", task_id=task_id, user_id=user.id)
    reason = "disconnected"
    try:
        while True:
            raw = await ws.receive_text()
            try:
                await services.limiter.check(
                    f"ws:{task_id}", MAX_MESSAGES_PER_MINUTE, 60
                )
            except RateLimitedError:
                reason = "rate_limited"
                await ws.close(CLOSE_RATE_LIMITED, reason="too many messages")
                break
            try:
                msg = ClientMessage.validate_json(raw)
            except ValidationError as exc:
                log.warning("bad_client_message", task_id=task_id, error=str(exc)[:500])
                continue
            if isinstance(msg, ToolResultMessage):
                if not service.submit_tool_result(task_id, msg.call_id, msg.result):
                    log.info("stale_tool_result", task_id=task_id, call_id=msg.call_id)
            elif isinstance(msg, UserReplyMessage):
                if not service.submit_answer(task_id, msg.request_id, msg.answer):
                    log.info("stale_user_reply", task_id=task_id, request_id=msg.request_id)
            elif isinstance(msg, ConfirmationReplyMessage):
                if not service.submit_decision(task_id, msg.request_id, msg.approved):
                    log.info("stale_confirmation", task_id=task_id, request_id=msg.request_id)
                else:
                    log.info(
                        "confirmation_decided",
                        task_id=task_id,
                        user_id=user.id,
                        approved=msg.approved,
                    )
            else:
                await service.stop(task_id)
    except WebSocketDisconnect:
        pass
    finally:
        # Shielded: if this handler is cancelled (server shutdown), the
        # session must still be released and recorded.
        await asyncio.shield(service.detach(task_id, reason))
        log.info("session_detached", task_id=task_id)
