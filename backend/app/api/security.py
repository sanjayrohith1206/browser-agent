"""Request-level protections, applied before any route runs.

- Host check: only the configured host names are accepted (blocks DNS
  rebinding; loopback by default).
- Origin check: browser requests must come from the extension. Requests
  without an Origin header come from non-browser clients (curl, tests) and
  still need a valid sign-in token for anything but health checks.
- Body size limit, and security headers on every response.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

EXTENSION_SCHEME = "chrome-extension://"
MAX_BODY_BYTES = 256 * 1024

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


class OriginPolicy:
    def __init__(self, allowed_extension_ids: Iterable[str]) -> None:
        self._allowed = frozenset(f"{EXTENSION_SCHEME}{i}" for i in allowed_extension_ids)

    def allows(self, origin: str | None) -> bool:
        if origin is None:
            return True
        if self._allowed:
            return origin in self._allowed
        return origin.startswith(EXTENSION_SCHEME)


class GuardMiddleware:
    """Rejects HTTP and WebSocket requests with a foreign Origin or an
    unexpected Host, and HTTP bodies over the size limit."""

    def __init__(
        self,
        app: ASGIApp,
        policy: OriginPolicy,
        allowed_hosts: Iterable[str],
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self.app = app
        self.policy = policy
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host_ok = "*" in self.allowed_hosts or (
            _hostname(headers.get("host", "")).lower() in self.allowed_hosts
        )
        if not host_ok or not self.policy.allows(headers.get("origin")):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            await _respond(send, 403, b'{"detail":"forbidden origin"}')
            return

        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body_bytes):
            await _respond(send, 413, b'{"detail":"request too large"}')
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise BodyTooLargeError
            return message

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in SECURITY_HEADERS if k not in existing
                ]
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except BodyTooLargeError:
            await _respond(send, 413, b'{"detail":"request too large"}')


class BodyTooLargeError(Exception):
    pass


async def _respond(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *SECURITY_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _hostname(host_header: str) -> str:
    """Strip the port from a Host header value, handling IPv6 literals."""
    if host_header.startswith("["):
        return host_header[: host_header.find("]") + 1]
    return host_header.split(":", 1)[0]
