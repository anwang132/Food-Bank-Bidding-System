"""Pure ASGI middleware: request ids, access logs, origin policy, body size and a
small per-process IP rate limiter for unauthenticated auth endpoints.

File uploads (``UPLOAD_PATHS``) get ``max_upload_bytes`` instead of the JSON limit
and are also counted while streaming. Other bodies without Content-Length
(chunked) are bounded by nginx's ``client_max_body_size``; ``X-Real-IP`` is
trusted because API ports are bound to localhost and reached only through nginx."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logs import request_id_var, user_id_var

log = logging.getLogger("app.access")
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
RATE_LIMITED_PATHS = {"/api/auth/login", "/api/auth/register"}
UPLOAD_PATHS = {"/api/planning/documents"}


def _error(status: int, code: str, message: str, rid: str) -> tuple[dict[str, Any], bytes]:
    body = json.dumps({"error": {"code": code, "message": message, "request_id": rid}}).encode()
    start = {
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"x-request-id", rid.encode())],
    }
    return start, body


class _IpLimiter:
    """Fixed one-minute windows per client address, bounded to ``max_keys`` entries."""

    def __init__(self, per_minute: int, max_keys: int = 10_000) -> None:
        self.per_minute = per_minute
        self.max_keys = max_keys
        self._hits: OrderedDict[str, tuple[int, int]] = OrderedDict()

    def allow(self, key: str) -> bool:
        window = int(time.monotonic() // 60)
        w, count = self._hits.pop(key, (window, 0))
        count = count + 1 if w == window else 1
        self._hits[key] = (window, count)
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)
        return count <= self.per_minute


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: list[str],
        max_body_bytes: int,
        max_upload_bytes: int,
        ip_rate_limit_per_minute: int,
        instance_id: str,
    ) -> None:
        self.app = app
        self.allowed_origins = set(allowed_origins)
        self.max_body = max_body_bytes
        self.max_upload = max_upload_bytes
        self.limiter = _IpLimiter(ip_rate_limit_per_minute)
        self.instance = instance_id.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        incoming = headers.get("x-request-id", "")
        rid = incoming if _RID.match(incoming) else uuid.uuid4().hex
        request_id_var.set(rid)
        user_id_var.set(None)
        method, path = scope["method"], scope["path"]
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def reject(status: int, code: str, message: str) -> None:
            start, body = _error(status, code, message, rid)
            status_holder["status"] = status
            await send(start)
            await send({"type": "http.response.body", "body": body})

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [
                    *message["headers"],
                    (b"x-request-id", rid.encode()),
                    (b"x-instance-id", self.instance),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        try:
            origin = headers.get("origin")
            if method in UNSAFE and origin is not None and origin not in self.allowed_origins:
                await reject(403, "ORIGIN_NOT_ALLOWED", "Request origin is not allowed")
                return
            limit = self.max_upload if path in UPLOAD_PATHS else self.max_body
            length = headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                await reject(413, "PAYLOAD_TOO_LARGE", "Request body is too large")
                return
            if method == "POST" and path in RATE_LIMITED_PATHS:
                client = scope.get("client")
                ip = headers.get("x-real-ip") or (client[0] if client else "unknown")
                if not self.limiter.allow(ip):
                    await reject(429, "RATE_LIMITED", "Too many attempts. Try again in a minute.")
                    return
            await self.app(scope, receive, send_wrapper)
        finally:
            if path not in ("/healthz", "/readyz"):
                log.info(
                    "request",
                    extra={
                        "method": method,
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
