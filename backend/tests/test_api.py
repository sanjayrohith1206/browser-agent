"""HTTP + WebSocket integration tests against the real FastAPI app."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.kv import MemoryKV
from app.llm.provider import LangChainProvider
from app.main import create_app
from tests.fakes import PAGE, ScriptedChatModel, text_turn, tool_turn

# Pass copies to websocket_connect: it writes the subprotocol header into
# the dict it is given.
EXT = {"origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
PASSWORD = "correct horse battery"


@pytest.fixture
def model() -> ScriptedChatModel:
    return ScriptedChatModel(
        script=[
            tool_turn(("get_page_content", {})),
            text_turn("The page is about browsers."),
        ]
    )


@pytest.fixture
def client(settings: Settings, model: ScriptedChatModel) -> Iterator[TestClient]:
    app = create_app(settings, provider=LangChainProvider("test", model), kv=MemoryKV())
    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str = "me@example.com") -> dict[str, str]:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Me"},
        headers=EXT,
    )
    assert res.status_code == 201, res.text
    return {**EXT, "authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    return register(client)


def ws_protocols(headers: dict[str, str]) -> list[str]:
    return ["browser-agent.v1", "auth." + headers["authorization"].removeprefix("Bearer ")]


def create_task(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    res = client.post(
        "/api/tasks",
        json={
            "prompt": "Read this page and summarize it.",
            "page": {"tab_id": 7, "url": PAGE["url"], "title": PAGE["title"]},
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()  # type: ignore[no-any-return]


def test_health(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "anthropic"
    assert client.get("/readyz").json() == {"database": True, "kv": True}


def test_health_without_llm_credentials(settings: Settings) -> None:
    with TestClient(create_app(settings, kv=MemoryKV())) as c:
        body = c.get("/healthz").json()
        assert body["status"] == "degraded"
        assert "LLM_API_KEY" in body["detail"]
        headers = register(c)
        res = c.post("/api/tasks", json={"prompt": "hi"}, headers=headers)
        assert res.status_code == 503
        # History and memory work without a model.
        assert c.get("/api/tasks", headers=headers).status_code == 200
        assert c.get("/api/memory", headers=headers).status_code == 200


def test_rejects_foreign_origins_and_hosts(client: TestClient, auth: dict[str, str]) -> None:
    evil = {**auth, "origin": "https://evil.example"}
    assert client.get("/api/tasks", headers=evil).status_code == 403
    assert client.get("/api/tasks", headers={**auth, "host": "evil.example"}).status_code == 403
    assert client.get("/api/tasks", headers=auth).status_code == 200
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/tasks/x", headers={"origin": "https://evil.example"}),
    ):
        pass
    assert exc.value.code == 4403


def test_requires_sign_in(client: TestClient, auth: dict[str, str]) -> None:
    for method, path in [
        ("GET", "/api/tasks"),
        ("POST", "/api/tasks"),
        ("GET", "/api/tasks/x"),
        ("GET", "/api/memory"),
        ("GET", "/api/auth/me"),
    ]:
        assert client.request(method, path, headers=EXT).status_code == 401, path
    bad = {**EXT, "authorization": "Bearer nope"}
    assert client.get("/api/tasks", headers=bad).status_code == 401
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            "/ws/tasks/x", headers=dict(EXT), subprotocols=["browser-agent.v1"]
        ) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4401


def test_security_headers_and_body_limit(client: TestClient, auth: dict[str, str]) -> None:
    res = client.get("/healthz")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "no-store"
    big = client.post("/api/tasks", content=b"x" * 300_000, headers=auth)
    assert big.status_code == 413


def test_validation(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/api/tasks", json={"prompt": "   "}, headers=auth).status_code == 422
    assert client.post("/api/tasks", json={"prompt": "x" * 9000}, headers=auth).status_code == 422
    assert client.get("/api/tasks/missing", headers=auth).status_code == 404
    assert client.post("/api/tasks/missing/stop", headers=auth).status_code == 404


def test_full_task_over_websocket(client: TestClient, auth: dict[str, str]) -> None:
    task = create_task(client, auth)
    received: list[dict[str, Any]] = []
    with client.websocket_connect(
        f"/ws/tasks/{task['id']}", headers=dict(EXT), subprotocols=ws_protocols(auth)
    ) as ws:
        assert ws.accepted_subprotocol == "browser-agent.v1"
        while True:
            event = ws.receive_json()
            received.append(event)
            if event["type"] == "tool_start":
                assert event["tool"] == "get_page_content"
                ws.send_json(
                    {
                        "type": "tool_result",
                        "call_id": event["call_id"],
                        "result": {"success": True, "tool": "get_page_content", "result": PAGE},
                    }
                )
            if event["type"] == "task_status" and event["status"] == "completed":
                break

    types = [e["type"] for e in received]
    assert types[:2] == ["task_status", "task_status"]
    assert "tool_result" in types and "agent_message_delta" in types
    final = next(e for e in received if e["type"] == "agent_message")
    assert final["message"] == "The page is about browsers."

    stored = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
    assert stored["status"] == "completed"
    assert stored["result"] == "The page is about browsers."
    assert stored["steps"][0]["success"] is True
    listed = client.get("/api/tasks", headers=auth).json()["tasks"]
    assert [(t["id"], t["goal"]) for t in listed] == [
        (task["id"], "Read this page and summarize it.")
    ]

    # History: stored events replay the conversation without stream fragments.
    events = client.get(f"/api/tasks/{task['id']}/events", headers=auth).json()["events"]
    assert "agent_message_delta" not in [e["type"] for e in events]
    assert events[-1]["status"] == "completed"


def test_tasks_are_private(client: TestClient, auth: dict[str, str], settings: Settings) -> None:
    task = create_task(client, auth)
    # A second account (registration is closed after the first by default).
    client.app.state.services.settings.allow_registration = True  # type: ignore[attr-defined]
    other = register(client, "other@example.com")
    assert client.get(f"/api/tasks/{task['id']}", headers=other).status_code == 404
    assert client.get(f"/api/tasks/{task['id']}/events", headers=other).status_code == 404
    assert client.post(f"/api/tasks/{task['id']}/stop", headers=other).status_code == 404
    assert client.delete(f"/api/tasks/{task['id']}", headers=other).status_code == 404
    assert client.get("/api/tasks", headers=other).json()["tasks"] == []
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            f"/ws/tasks/{task['id']}", headers=dict(EXT), subprotocols=ws_protocols(other)
        ) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4404


def test_websocket_errors(client: TestClient, auth: dict[str, str]) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            "/ws/tasks/missing", headers=dict(EXT), subprotocols=ws_protocols(auth)
        ) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4404


def test_stop_and_delete_queued_task(client: TestClient, auth: dict[str, str]) -> None:
    task = create_task(client, auth)
    res = client.post(f"/api/tasks/{task['id']}/stop", headers=auth)
    assert res.json()["status"] == "cancelled"
    assert client.delete(f"/api/tasks/{task['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/tasks/{task['id']}", headers=auth).status_code == 404


def test_running_task_limit(client: TestClient, auth: dict[str, str], settings: Settings) -> None:
    services = client.app.state.services  # type: ignore[attr-defined]
    services.settings.max_running_tasks_per_user = 1
    first = create_task(client, auth)

    async def mark_running() -> None:
        await services.db.tasks.update(first["id"], status="running")

    client.portal.call(mark_running)  # type: ignore[union-attr]
    res = client.post("/api/tasks", json={"prompt": "another"}, headers=auth)
    assert res.status_code == 429
