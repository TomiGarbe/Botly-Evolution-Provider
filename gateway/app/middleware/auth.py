import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.instance_auth import authenticate_instance_token

logger = get_logger(__name__)

_PUBLIC_PATHS = {"/health", "/webhooks/evolution"}
_PUBLIC_PREFIXES = ("/media/upload/",)


class AuthMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def _bearer_token(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return re.sub(r"^Bearer\s+", "", raw, flags=re.IGNORECASE).strip()

    @staticmethod
    def _path_instance(path: str) -> str | None:
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "instances":
            return parts[1]
        if len(parts) >= 2 and parts[0] == "messages":
            return parts[1]
        return None

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        if any(request.url.path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        request.state.auth_mode = "none"
        request.state.auth_instance = None
        request.state.is_admin = False

        api_key = str(request.headers.get("X-API-Key") or "").strip()
        bearer_token = self._bearer_token(request.headers.get("Authorization"))
        settings = get_settings()

        if api_key and api_key == settings.gateway_api_key:
            request.state.auth_mode = "admin"
            request.state.is_admin = True
            return await call_next(request)

        if bearer_token:
            auth = authenticate_instance_token(bearer_token)
            if not auth:
                logger.warning(
                    "invalid_auth",
                    path=request.url.path,
                    reason="invalid_or_revoked_token",
                )
                logger.warning(
                    "auth_failed_invalid_token",
                    path=request.url.path,
                    ip=request.client.host if request.client else "unknown",
                    token_prefix=bearer_token[:8],
                )
                return JSONResponse(status_code=401, content={"detail": "Bearer token invalido o revocado"})

            path_instance = self._path_instance(request.url.path)
            if path_instance and path_instance != auth["instance"]:
                logger.warning(
                    "invalid_auth",
                    path=request.url.path,
                    token_instance=auth["instance"],
                    path_instance=path_instance,
                )
                logger.warning(
                    "auth_failed_instance_mismatch",
                    path=request.url.path,
                    token_instance=auth["instance"],
                    path_instance=path_instance,
                )
                return JSONResponse(status_code=403, content={"detail": "Token no autorizado para esta instancia"})

            if request.url.path.startswith("/instances/") and not path_instance:
                return JSONResponse(status_code=403, content={"detail": "Token de instancia no puede acceder a recursos globales"})

            request.state.auth_mode = "instance"
            request.state.auth_instance = auth["instance"]
            logger.info("auth_success_instance", path=request.url.path, instance=auth["instance"])
            return await call_next(request)

        logger.warning(
            "invalid_auth",
            path=request.url.path,
            reason="missing_credentials" if not api_key else "admin_key_mismatch",
        )
        logger.warning(
            "auth_failed_missing_credentials",
            path=request.url.path,
            ip=request.client.host if request.client else "unknown",
        )
        if api_key and api_key != settings.gateway_api_key:
            logger.warning(
                "auth_failed_admin_key_mismatch",
                path=request.url.path,
                ip=request.client.host if request.client else "unknown",
            )
        return JSONResponse(
            status_code=401,
            content={"detail": "Credenciales ausentes o invalidas. Usa X-API-Key o Authorization: Bearer <token>"},
        )
