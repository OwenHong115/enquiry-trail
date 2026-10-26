"""P3a ingress middleware: exact Origin checks and a body bound.

Pure ASGI, added after the request-id middleware so it sits
outside it: rejections it makes itself still carry an
X-Request-ID and the normal error envelope.

Origin: unsafe methods only, and never the public health paths.
An absent Origin is allowed (non-browser clients; CSRF still
protects session writes); a present Origin must match exactly,
and "null" never matches. No CORS headers are added.

Body: an oversized Content-Length is rejected before the app
runs; otherwise the receive channel is wrapped and counted, so
chunked or lying bodies abort at the same bound without ever
buffering the whole oversized body.
"""

from __future__ import annotations

import uuid

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.errors import error_body
from app.logging import log_event
from app.settings import Settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PUBLIC_PREFIX = "/api/v1/health/"


# Oversized bodies are rejected by IngressMiddleware itself,
# before the downstream app ever runs; no exception crosses it.


def _envelope(scope, status: int, code: str, message: str) -> JSONResponse:
    request_id = scope["state"]["request_id"]
    response = JSONResponse(
        status_code=status, content=error_body(code, message, request_id)
    )
    response.headers["X-Request-ID"] = request_id
    return response


class IngressMiddleware:
    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.allowed_origins = set(settings.resolved_allowed_origins())
        self.max_bytes = settings.max_request_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["request_id"] = str(uuid.uuid4())
        headers = Headers(scope=scope)
        method = scope.get("method", "")
        path = scope.get("path", "")

        if method in _UNSAFE_METHODS and not path.startswith(_PUBLIC_PREFIX):
            origin = headers.get("origin")
            if origin is not None and origin not in self.allowed_origins:
                response = _envelope(
                    scope, 403, "origin_invalid", "Origin is not allowed"
                )
                log_event(
                    "http.request",
                    request_id=scope["state"]["request_id"],
                    method=method, path=path, status=403,
                )
                await response(scope, receive, send)
                return

        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > self.max_bytes:
                response = _envelope(
                    scope, 413, "payload_too_large", "Request body too large"
                )
                log_event(
                    "http.request",
                    request_id=scope["state"]["request_id"],
                    method=method, path=path, status=413,
                )
                await response(scope, receive, send)
                return

        # Read the body here, counting as it streams: the moment
        # it passes the bound the request is rejected and the
        # downstream app never runs. An in-bound body (at most the
        # limit) is replayed to the app unchanged; an oversized
        # stream is aborted early, never buffered in full.
        messages: list[dict] = []
        seen = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    response = _envelope(
                        scope, 413, "payload_too_large",
                        "Request body too large",
                    )
                    log_event(
                        "http.request",
                        request_id=scope["state"]["request_id"],
                        method=method, path=path, status=413,
                    )
                    await response(scope, receive, send)
                    return
                messages.append(message)
                if not message.get("more_body", False):
                    break
            else:
                messages.append(message)
                break

        replay = iter(messages)

        async def replay_receive():
            return next(replay, {"type": "http.request", "body": b""})

        await self.app(scope, replay_receive, send)
