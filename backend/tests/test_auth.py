"""Accounts, sign-in, rate limits and the memory API."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password, password_problem, verify_password
from app.config import Settings
from app.kv import MemoryKV
from app.main import create_app

EXT = {"origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
PASSWORD = "correct horse battery"


def test_password_hashing() -> None:
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong password", stored)
    assert not verify_password(PASSWORD, "garbage")
    assert hash_password(PASSWORD) != stored  # salted
    assert password_problem("short") is not None
    assert password_problem("aaaaaaaaaaaa") is not None
    assert password_problem(PASSWORD) is None


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, kv=MemoryKV())) as c:
        yield c


def signup(client: TestClient, email: str = "me@example.com", password: str = PASSWORD) -> str:
    res = client.post(
        "/api/auth/register", json={"email": email, "password": password}, headers=EXT
    )
    assert res.status_code == 201, res.text
    return str(res.json()["token"])


def bearer(token: str) -> dict[str, str]:
    return {**EXT, "authorization": f"Bearer {token}"}


def test_first_account_then_registration_closes(client: TestClient) -> None:
    assert client.get("/api/auth/status").json() == {"registration_open": True}
    token = signup(client)
    me = client.get("/api/auth/me", headers=bearer(token)).json()
    assert me["email"] == "me@example.com" and "password" not in str(me)

    assert client.get("/api/auth/status").json() == {"registration_open": False}
    res = client.post(
        "/api/auth/register", json={"email": "b@example.com", "password": PASSWORD}, headers=EXT
    )
    assert res.status_code == 403


def test_open_registration(settings: Settings) -> None:
    settings = settings.model_copy(update={"allow_registration": True})
    with TestClient(create_app(settings, kv=MemoryKV())) as c:
        signup(c)
        signup(c, "b@example.com")
        res = c.post(
            "/api/auth/register",
            json={"email": "B@example.com", "password": PASSWORD},
            headers=EXT,
        )
        assert res.status_code == 409


def test_registration_validation(client: TestClient) -> None:
    for body in (
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a@example.com", "password": "short"},
    ):
        assert client.post("/api/auth/register", json=body, headers=EXT).status_code == 422


def test_login_and_logout(client: TestClient) -> None:
    signup(client)
    res = client.post(
        "/api/auth/login", json={"email": "ME@example.com", "password": PASSWORD}, headers=EXT
    )
    assert res.status_code == 200
    token = res.json()["token"]
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200

    wrong = client.post(
        "/api/auth/login", json={"email": "me@example.com", "password": "nope nope nope"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "who@example.com", "password": PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # doesn't reveal which accounts exist

    assert client.post("/api/auth/logout", headers=bearer(token)).status_code == 204
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_login_attempts_are_rate_limited(client: TestClient, settings: Settings) -> None:
    signup(client)
    body = {"email": "me@example.com", "password": "wrong password!"}
    codes = [
        client.post("/api/auth/login", json=body).status_code
        for _ in range(settings.rate_limit_logins_per_15m + 1)
    ]
    assert codes[:-1] == [401] * settings.rate_limit_logins_per_15m
    assert codes[-1] == 429
    limited = client.post("/api/auth/login", json={**body, "password": PASSWORD})
    assert limited.status_code == 429 and limited.headers["retry-after"] == "900"


def test_requests_are_rate_limited(settings: Settings) -> None:
    settings = settings.model_copy(update={"rate_limit_requests_per_minute": 3})
    with TestClient(create_app(settings, kv=MemoryKV())) as c:
        headers = bearer(signup(c))
        codes = [c.get("/api/memory", headers=headers).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]


def test_memory_api(client: TestClient) -> None:
    headers = bearer(signup(client))
    added = client.post("/api/memory", json={"content": "  I live in  Pune "}, headers=headers)
    assert added.status_code == 201 and added.json()["content"] == "I live in Pune"
    item_id = added.json()["id"]

    edited = client.patch(
        f"/api/memory/{item_id}", json={"content": "I live in Mumbai"}, headers=headers
    )
    assert edited.json()["content"] == "I live in Mumbai"
    listed = client.get("/api/memory", headers=headers).json()
    assert [i["content"] for i in listed["items"]] == ["I live in Mumbai"]
    assert listed["max_items"] == 100

    assert client.post("/api/memory", json={"content": "   "}, headers=headers).status_code == 422
    assert (
        client.post("/api/memory", json={"content": "x" * 501}, headers=headers).status_code == 422
    )
    assert client.delete(f"/api/memory/{item_id}", headers=headers).status_code == 204
    assert client.delete(f"/api/memory/{item_id}", headers=headers).status_code == 404


def test_docs_are_hidden_in_production(settings: Settings) -> None:
    assert TestClient(create_app(settings, kv=MemoryKV())).get("/openapi.json").status_code == 200
    production = Settings.model_construct(**{**settings.model_dump(), "environment": "production"})
    with TestClient(create_app(production, kv=MemoryKV())) as c:
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/docs").status_code == 404


def test_production_settings_are_checked(settings: Settings) -> None:
    with pytest.raises(ValueError, match="DATABASE_URL must point to PostgreSQL"):
        Settings(_env_file=None, environment="production")  # type: ignore[call-arg]
    ok = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="production",
        database_url="postgresql+asyncpg://u:p@db/agent",
        redis_url="redis://redis:6379/0",
        data_encryption_keys="k",
        allowed_extension_ids=["abc"],
    )
    assert ok.is_production
