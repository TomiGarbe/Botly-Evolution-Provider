import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Loguea cada request con tenant/instancia, duración y status code."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/ready", "/webhooks/events"}:
            return await call_next(request)

        request_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()

        # Extraer instanceName de la ruta si viene (/instances/{name}/...)
        path_parts = request.url.path.strip("/").split("/")
        instance_name = None
        if len(path_parts) >= 2 and path_parts[0] == "instances":
            instance_name = path_parts[1]
        elif len(path_parts) >= 2 and path_parts[0] == "messages":
            instance_name = path_parts[1]

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            instance=instance_name,
            method=request.method,
            path=request.url.path,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.error("request_failed", duration_ms=duration_ms, error=str(exc))
            raise
        response.headers["X-Request-Id"] = request_id

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        if response.status_code >= 500:
            logger.error("request", status=response.status_code, duration_ms=duration_ms)
        elif response.status_code >= 400:
            logger.warning("request", status=response.status_code, duration_ms=duration_ms)
        elif duration_ms >= 1200:
            logger.info("request_slow", status=response.status_code, duration_ms=duration_ms)
        else:
            logger.debug("request", status=response.status_code, duration_ms=duration_ms)

        return response
