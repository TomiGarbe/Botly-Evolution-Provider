from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

AsgiReceive = Callable[[], Awaitable[dict[str, Any]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


class CorsDiagnosticsMiddleware:
    """Logs CORS decisions and response headers when CORS_DEBUG=true."""

    def __init__(self, app: Callable[[dict[str, Any], AsgiReceive, AsgiSend], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: AsgiReceive, send: AsgiSend) -> None:
        settings = get_settings()
        if scope.get("type") != "http" or not settings.cors_debug:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        origin = headers.get("origin", "")
        if not origin:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        requested_method = headers.get("access-control-request-method", "")
        allowed = settings.is_cors_origin_allowed(origin)

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                response_headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in message.get("headers", [])
                    if key.decode("latin-1").lower().startswith("access-control-")
                    or key.decode("latin-1").lower() == "vary"
                }
                logger.info(
                    "cors_diagnostic",
                    origin=origin,
                    method=method,
                    path=path,
                    requested_method=requested_method,
                    origin_allowed=allowed,
                    status=message.get("status"),
                    response_headers=response_headers,
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)
