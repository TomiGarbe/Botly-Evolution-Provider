from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger


logger = get_logger(__name__)
_LOCK = threading.Lock()


class InstagramOAuthError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        operation: str | None = None,
        provider_http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # These values are operational metadata only. They intentionally omit
        # request/response bodies, codes, tokens and credentials.
        self.operation = operation
        self.provider_http_status = provider_http_status


@dataclass(frozen=True)
class InstagramOAuthIntent:
    connection_id: str
    client_id: str
    actor_id: str
    provider_id: str = "meta"
    channel_type: str = "instagram"
    # Opt-in only. API callers retain the established JSON callback response.
    ui_return: bool = False


@dataclass(frozen=True)
class InstagramAccount:
    provider_account_id: str
    username: str | None = None
    display_name: str | None = None
    account_type: str | None = None

    def metadata(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "username": self.username,
                "displayName": self.display_name,
                "accountType": self.account_type,
            }.items()
            if value
        }


@dataclass(frozen=True)
class InstagramOAuthToken:
    access_token: str
    expires_at: str | None
    granted_scopes: tuple[str, ...]


class InstagramOAuthStateStore:
    """Small durable, single-use OAuth state store suitable for multi-worker use."""

    def __init__(self, path: str | Path | None = None, *, ttl_seconds: int | None = None) -> None:
        settings = get_settings()
        self._path = Path(path or settings.instagram_oauth_state_path)
        self._ttl_seconds = int(ttl_seconds if ttl_seconds is not None else settings.instagram_oauth_state_ttl_seconds)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"states": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {"states": raw.get("states", {})} if isinstance(raw, dict) and isinstance(raw.get("states"), dict) else {"states": {}}
        except Exception:
            return {"states": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self._path)

    @staticmethod
    def _hash(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    def create(self, intent: InstagramOAuthIntent) -> str:
        if self._ttl_seconds <= 0:
            raise InstagramOAuthError("Instagram OAuth state TTL must be positive", status_code=500)
        state = secrets.token_urlsafe(32)
        now = int(time.time())
        record = {
            "connectionId": intent.connection_id,
            "clientId": intent.client_id,
            "actorId": intent.actor_id,
            "provider": intent.provider_id,
            "channelType": intent.channel_type,
            "uiReturn": intent.ui_return,
            "createdAt": now,
            "expiresAt": now + self._ttl_seconds,
        }
        with _LOCK:
            payload = self._load()
            payload["states"] = {
                key: value
                for key, value in payload["states"].items()
                if isinstance(value, dict) and int(value.get("expiresAt") or 0) > now
            }
            payload["states"][self._hash(state)] = record
            self._save(payload)
        return state

    def consume(self, state: str | None) -> InstagramOAuthIntent:
        candidate = str(state or "")
        if not candidate:
            raise InstagramOAuthError("OAuth state is required")
        now = int(time.time())
        with _LOCK:
            payload = self._load()
            record = payload["states"].pop(self._hash(candidate), None)
            self._save(payload)
        if not isinstance(record, dict):
            raise InstagramOAuthError("OAuth state is invalid or already consumed")
        if int(record.get("expiresAt") or 0) <= now:
            raise InstagramOAuthError("OAuth state has expired")
        try:
            return InstagramOAuthIntent(
                connection_id=str(record["connectionId"]),
                client_id=str(record["clientId"]),
                actor_id=str(record["actorId"]),
                provider_id=str(record["provider"]),
                channel_type=str(record["channelType"]),
                ui_return=bool(record.get("uiReturn", False)),
            )
        except (KeyError, TypeError) as exc:
            raise InstagramOAuthError("OAuth state is malformed", status_code=500) from exc


class InstagramOAuthService:
    """Server-side OAuth and account discovery for Meta Instagram Login."""

    def __init__(self, *, settings_factory: Callable[[], Any] = get_settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings_factory = settings_factory
        self._client = client

    def requested_scopes(self) -> tuple[str, ...]:
        raw = str(getattr(self._settings_factory(), "instagram_oauth_scopes", "") or "")
        return tuple(dict.fromkeys(scope.strip() for scope in raw.replace(" ", ",").split(",") if scope.strip()))

    def validate_configuration(self) -> None:
        settings = self._settings_factory()
        self._ensure_configured(settings)
        if not self.requested_scopes():
            raise InstagramOAuthError("Instagram OAuth scopes are not configured", status_code=503)

    def authorization_url(self, *, state: str) -> str:
        settings = self._settings_factory()
        self.validate_configuration()
        scopes = self.requested_scopes()
        return f"{str(settings.instagram_oauth_authorize_url).rstrip('?')}?{urlencode({
            'client_id': settings.instagram_app_id,
            'redirect_uri': settings.meta_redirect_uri,
            'response_type': 'code',
            'scope': ','.join(scopes),
            'state': state,
        })}"

    async def exchange_code(self, code: str) -> InstagramOAuthToken:
        settings = self._settings_factory()
        self._ensure_configured(settings)
        if not str(code or "").strip():
            raise InstagramOAuthError("OAuth code is required")
        payload = await self._request_token(settings, code)
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise InstagramOAuthError("Meta returned no access token", status_code=502)
        expires_at = _expires_at(payload.get("expires_in"))
        granted = _scopes_from_response(payload) or self.requested_scopes()
        return InstagramOAuthToken(access_token=token, expires_at=expires_at, granted_scopes=granted)

    async def discover_account(self, access_token: str) -> InstagramAccount:
        settings = self._settings_factory()
        self._ensure_configured(settings)
        client, close = self._graph_client(settings)
        try:
            response = await client.get(
                "/me",
                params={"fields": "id,user_id,username,name,account_type"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code >= 400:
                raise InstagramOAuthError(
                    "Instagram account discovery failed",
                    status_code=502,
                    operation="GET /me",
                    provider_http_status=response.status_code,
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise InstagramOAuthError(
                    "Instagram account discovery returned malformed data",
                    status_code=502,
                    operation="GET /me",
                    provider_http_status=response.status_code,
                ) from exc
        except httpx.TimeoutException as exc:
            raise InstagramOAuthError("Timeout during Instagram account discovery", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise InstagramOAuthError("Transport error during Instagram account discovery", status_code=502) from exc
        finally:
            if close:
                await client.aclose()
        if not isinstance(payload, dict):
            raise InstagramOAuthError("Instagram account discovery returned malformed data", status_code=502)
        # Instagram Login returns both an app-scoped ``id`` and the Instagram
        # professional-account ``user_id``. Meta places the latter in the
        # ``entry.id`` of Instagram messaging webhooks, so it is the stable
        # provider-account key used by the webhook resolver. Keep ``id`` only
        # as a compatibility fallback for responses that omit ``user_id``.
        account_id = str(payload.get("user_id") or payload.get("id") or "").strip()
        if not account_id:
            raise InstagramOAuthError("No supported Instagram professional account was discovered", status_code=422)
        account_type = _optional_str(payload.get("account_type"))
        if account_type and account_type.upper() not in {"BUSINESS", "CREATOR"}:
            raise InstagramOAuthError("The discovered Instagram account is not a supported professional account", status_code=422)
        return InstagramAccount(
            provider_account_id=account_id,
            username=_optional_str(payload.get("username")),
            display_name=_optional_str(payload.get("name")),
            account_type=account_type,
        )

    async def _request_token(self, settings: Any, code: str) -> dict[str, Any]:
        client, close = self._token_client(settings)
        try:
            response = await client.post(
                "",
                data={
                    "client_id": settings.instagram_app_id,
                    "client_secret": settings.instagram_app_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.meta_redirect_uri,
                    "code": code,
                },
            )
            if response.status_code >= 400:
                raise InstagramOAuthError(
                    "Instagram authorization code exchange failed",
                    status_code=502,
                    operation="POST /oauth/access_token",
                    provider_http_status=response.status_code,
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise InstagramOAuthError(
                    "Instagram authorization code exchange returned malformed data",
                    status_code=502,
                    operation="POST /oauth/access_token",
                    provider_http_status=response.status_code,
                ) from exc
            if not isinstance(payload, dict):
                raise InstagramOAuthError("Instagram authorization code exchange returned malformed data", status_code=502)
            return payload
        except httpx.TimeoutException as exc:
            raise InstagramOAuthError("Timeout during Instagram authorization code exchange", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise InstagramOAuthError("Transport error during Instagram authorization code exchange", status_code=502) from exc
        finally:
            if close:
                await client.aclose()

    def _ensure_configured(self, settings: Any) -> None:
        missing = [name for name, value in {
            "INSTAGRAM_APP_ID": getattr(settings, "instagram_app_id", ""),
            "INSTAGRAM_APP_SECRET": getattr(settings, "instagram_app_secret", ""),
            "META_REDIRECT_URI": getattr(settings, "meta_redirect_uri", ""),
        }.items() if not str(value or "").strip()]
        if missing:
            raise InstagramOAuthError(f"Instagram OAuth is not configured: missing {', '.join(missing)}", status_code=503)

    def _token_client(self, settings: Any) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=str(settings.instagram_oauth_token_url), timeout=httpx.Timeout(float(getattr(settings, "meta_signup_timeout_seconds", 30)))), True

    def _graph_client(self, settings: Any) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=str(settings.instagram_graph_api_url), timeout=httpx.Timeout(float(getattr(settings, "meta_signup_timeout_seconds", 30)))), True


def _expires_at(expires_in: Any) -> str | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scopes_from_response(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("scopes", payload.get("scope", ()))
    values = raw.replace(" ", ",").split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else ()
    return tuple(dict.fromkeys(str(scope).strip() for scope in values if str(scope).strip()))


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
