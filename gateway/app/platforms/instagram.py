"""Instagram Graph transport for credentials issued by Instagram Login."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger


logger = get_logger(__name__)


class InstagramGraphError(Exception):
    """Safe provider error contract for Instagram Graph delivery."""

    def __init__(self, message: str, *, status_code: int = 502, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail or {}


class InstagramGraphClient:
    """Deliver Instagram messages without reusing the Facebook/WhatsApp client."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        settings_factory: Callable[[], Any] = get_settings,
    ) -> None:
        self._client = client
        self._settings_factory = settings_factory

    async def send_message(
        self,
        *,
        instagram_account_id: str,
        access_token: str,
        recipient_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        account_id = str(instagram_account_id or "").strip()
        token = str(access_token or "").strip()
        recipient = str(recipient_id or "").strip()
        if not account_id:
            raise ValueError("instagram_account_id is required")
        if not token:
            raise ValueError("access_token is required")
        if not recipient:
            raise ValueError("recipient_id is required")

        client, close = self._graph_client()
        try:
            response = await client.post(
                self._message_path(account_id),
                headers={"Authorization": f"Bearer {token}"},
                json={"recipient": {"id": recipient}, "message": message},
            )
            if response.status_code >= 400:
                detail = self._error_detail(response)
                logger.warning(
                    "instagram_graph_error",
                    method="POST",
                    path=self._message_path(account_id),
                    status=response.status_code,
                    error_type=detail.get("type"),
                    error_code=detail.get("code"),
                )
                raise InstagramGraphError(
                    "Instagram Graph rejected the outbound request.",
                    status_code=response.status_code if response.status_code < 500 else 502,
                    detail=detail,
                )
            if not response.content:
                return {"ok": True}
            payload = response.json()
            return payload if isinstance(payload, dict) else {"ok": True}
        except httpx.TimeoutException as exc:
            raise InstagramGraphError("Timeout communicating with Instagram Graph.", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise InstagramGraphError("Transport error communicating with Instagram Graph.", status_code=502) from exc
        finally:
            if close:
                await client.aclose()

    def _graph_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        settings = self._settings_factory()
        base_url = str(settings.instagram_graph_api_url or "").strip().rstrip("/")
        if not base_url:
            raise InstagramGraphError("Instagram Graph API URL is not configured.", status_code=503)
        return httpx.AsyncClient(
            base_url=f"{base_url}/",
            timeout=httpx.Timeout(float(settings.meta_signup_timeout_seconds)),
        ), True

    def _message_path(self, instagram_account_id: str) -> str:
        settings = self._settings_factory()
        version = str(settings.instagram_graph_api_version or "").strip().strip("/")
        if not version or "/" in version:
            raise InstagramGraphError("Instagram Graph API version is invalid.", status_code=503)
        return f"{version}/{instagram_account_id}/messages"

    @staticmethod
    def _error_detail(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return {"type": error.get("type"), "code": error.get("code")}
        return {}
