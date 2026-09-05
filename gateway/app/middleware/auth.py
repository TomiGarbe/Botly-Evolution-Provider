import hmac
import inspect

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.services.google_auth import get_auth_service
from app.services.instance_auth import authenticate_instance_token
from app.services.authorization import reviewer_endpoint_allowed


_PUBLIC_PATHS = {"/health", "/webhooks/evolution", "/webhooks/meta", "/auth/config", "/auth/google", "/auth/login", "/connections/meta/instagram/callback"}
_COOKIE = "botly_gateway_session"


def _gateway_api_key(request: Request) -> str:
    direct = str(request.headers.get("x-api-key") or "").strip()
    if direct:
        return direct
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


class AuthMiddleware(BaseHTTPMiddleware):
    @staticmethod
    async def _next(call_next, request: Request):
        result = call_next(request)
        return await result if inspect.isawaitable(result) else result

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await self._next(call_next, request)

        if request.url.path in _PUBLIC_PATHS:
            return await self._next(call_next, request)
        expected_key = str(get_settings().gateway_api_key or "").strip()
        provided_key = _gateway_api_key(request)
        if expected_key and provided_key and hmac.compare_digest(provided_key, expected_key):
            request.state.auth_method = "gateway_api_key"
            return await self._next(call_next, request)

        # Instance API keys are the credentials Botly stores per Evolution
        # channel.  They intentionally have a narrower scope than the global
        # Gateway key: only the legacy unified endpoint and the versioned
        # canonical outbound endpoint accept them.
        instance_auth = authenticate_instance_token(provided_key)
        if instance_auth and (
            request.url.path.startswith("/messages/")
            or request.url.path == "/v1/outbound/messages"
        ):
            request.state.auth_method = "instance_api_key"
            request.state.auth_instance = instance_auth["instance"]
            return await self._next(call_next, request)

        user = get_auth_service().current_user(request.cookies.get(_COOKIE, ""))
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Iniciá sesión para continuar."})
        request.state.user = user
        if not reviewer_endpoint_allowed(request):
            return JSONResponse(status_code=403, content={"detail": "Esta cuenta sólo puede usar el flujo de Meta / WhatsApp Business."})
        return await self._next(call_next, request)
