import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.requests import CreateInstanceRequest
from app.services import evolution
from app.services.evolution import EvolutionError
from app.services.instance_auth import (
    create_or_regenerate_instance_key,
    delete_instance_key_record,
    ensure_instance_key,
    get_instance_key_info,
    revoke_instance_key,
)
from app.services.instance_webhooks import delete_all_instance_webhooks
from app.services.instances_contract import normalize_instance, normalize_instance_list, normalize_instance_status

logger = get_logger(__name__)
router = APIRouter(prefix="/instances", tags=["instances"])

_WEBHOOK_EVENTS = [
    "MESSAGES_UPSERT",
    "MESSAGES_UPDATE",
    "CONNECTION_UPDATE",
    "QRCODE_UPDATED",
    "SEND_MESSAGE",
]

_last_known_state: dict[str, str] = {}
_last_qr_at: dict[str, float] = {}


def _validate_instance_name(instance_name: str) -> str:
    cleaned = str(instance_name or "").strip()
    if not cleaned or cleaned == "-" or not all(ch.islower() or ch.isdigit() or ch == "_" for ch in cleaned):
        raise HTTPException(status_code=400, detail="Nombre de instancia invalido")
    return cleaned


def _apply_http_error(exc: Exception, *, fallback_status: int = 502) -> HTTPException:
    if isinstance(exc, EvolutionError):
        status_code = exc.status_code if 100 <= exc.status_code <= 599 else fallback_status
        return HTTPException(status_code=status_code, detail=str(exc))
    return HTTPException(status_code=fallback_status, detail=f"Evolution error: {exc}")


async def _configure_webhook_if_needed(instance_name: str) -> None:
    settings = get_settings()
    webhook_url = f"http://gateway:{settings.gateway_port}/webhooks/evolution"

    try:
        configured = await evolution.get_webhook(instance_name)
        current_url = configured.get("webhook", {}).get("url") if isinstance(configured, dict) else None
        if current_url == webhook_url:
            return
    except Exception as exc:
        logger.warning("webhook_lookup_failed", instance=instance_name, error=str(exc))

    try:
        await evolution.set_webhook(instance_name, webhook_url, _WEBHOOK_EVENTS)
        logger.info("webhook_configured", instance=instance_name, url=webhook_url)
    except Exception as exc:
        logger.warning("webhook_configure_failed", instance=instance_name, error=str(exc))


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_instance(body: CreateInstanceRequest):
    instance_name = _validate_instance_name(body.instance_name)
    try:
        result = await evolution.create_instance(
            instance_name=instance_name,
            qrcode=body.qrcode,
            token=body.token,
        )
        _last_known_state[instance_name] = "connecting"
    except Exception as exc:
        logger.error("create_instance_failed", instance=instance_name, error=str(exc))
        raise _apply_http_error(exc)

    if body.auto_configure_webhook:
        await _configure_webhook_if_needed(instance_name)
    ensure_instance_key(instance_name, instance_id=instance_name)

    if isinstance(result, dict):
        normalized = normalize_instance(result)
        if normalized:
            return normalized

    logger.warning("create_instance_response_unusable", instance=instance_name, raw=result)
    return {"id": instance_name, "name": instance_name, "status": "connecting"}


@router.get("/")
async def list_instances():
    try:
        instances = await evolution.fetch_instances()
    except Exception as exc:
        raise _apply_http_error(exc)

    normalized = normalize_instance_list(instances)
    for item in normalized:
        _last_known_state[item["name"]] = item["status"]
        ensure_instance_key(item["name"], instance_id=item.get("id"))
    return normalized


@router.get("/{instance_name}/state")
async def get_state(instance_name: str):
    instance_name = _validate_instance_name(instance_name)
    try:
        result = await evolution.get_connection_state(instance_name)
        state = normalize_instance_status(result.get("instance", {}).get("state"))
        _last_known_state[instance_name] = state
        return {"id": instance_name, "name": instance_name, "status": state}
    except Exception as exc:
        cached_state = _last_known_state.get(instance_name)
        if cached_state:
            logger.warning("state_fallback_from_cache", instance=instance_name, state=cached_state, error=str(exc))
            return {"id": instance_name, "name": instance_name, "status": cached_state, "stale": True}
        raise _apply_http_error(exc)


@router.get("/{instance_name}/qr")
async def get_qr(instance_name: str, refresh: bool = Query(default=False)):
    instance_name = _validate_instance_name(instance_name)
    try:
        state_data = await evolution.get_connection_state(instance_name)
        state = normalize_instance_status(state_data.get("instance", {}).get("state"))
        _last_known_state[instance_name] = state

        instance_payload = {"id": instance_name, "name": instance_name, "status": state}
        if state == "open":
            return {"instance": instance_payload, "qrcode": None}

        now = time.time()
        if refresh:
            await asyncio.sleep(0.4)

        qr_data = await evolution.get_qr(instance_name)
        _last_qr_at[instance_name] = now

        qrcode = qr_data.get("qrcode") if isinstance(qr_data, dict) else None
        if not isinstance(qrcode, dict):
            qrcode = {"base64": qr_data.get("base64"), "code": qr_data.get("code")} if isinstance(qr_data, dict) else {}

        return {
            "instance": instance_payload,
            "qrcode": qrcode,
            "fetchedAt": int(now),
            "nextRecommendedRefreshAt": int(now + 45),
        }
    except Exception as exc:
        logger.warning("qr_fetch_failed", instance=instance_name, error=str(exc))
        raise _apply_http_error(exc)


@router.post("/{instance_name}/reconnect")
async def reconnect(instance_name: str):
    instance_name = _validate_instance_name(instance_name)
    try:
        try:
            await evolution.restart_instance(instance_name)
        except EvolutionError as restart_exc:
            logger.warning("instance_restart_failed_fallback_qr", instance=instance_name, error=str(restart_exc))

        await asyncio.sleep(1.0)
        state = await evolution.get_connection_state(instance_name)
        normalized_state = normalize_instance_status(state.get("instance", {}).get("state"))
        _last_known_state[instance_name] = normalized_state

        payload: dict[str, Any] = {"instance": {"id": instance_name, "name": instance_name, "status": normalized_state}}
        if normalized_state != "open":
            payload["qr"] = await evolution.get_qr(instance_name)
        return payload
    except Exception as exc:
        raise _apply_http_error(exc)


@router.delete("/{instance_name}/logout")
async def logout(instance_name: str):
    instance_name = _validate_instance_name(instance_name)
    try:
        result = await evolution.logout_instance(instance_name)
        _last_known_state[instance_name] = "close"
        _last_qr_at.pop(instance_name, None)
        return {"ok": True, "instance": {"id": instance_name, "name": instance_name, "status": "close"}, "raw": result}
    except EvolutionError as exc:
        # Idempotencia operativa: si Evolution rechaza logout por estado/ausencia, tratamos como cerrado.
        if exc.status_code in (400, 404):
            logger.warning("logout_idempotent_fallback", instance=instance_name, error=str(exc))
            _last_known_state[instance_name] = "close"
            _last_qr_at.pop(instance_name, None)
            return {"ok": True, "instance": {"id": instance_name, "name": instance_name, "status": "close"}, "stale": True}
        raise _apply_http_error(exc)
    except Exception as exc:
        raise _apply_http_error(exc)


@router.delete("/{instance_name}")
async def delete(instance_name: str):
    instance_name = _validate_instance_name(instance_name)
    try:
        result = await evolution.delete_instance(instance_name)
        _last_known_state.pop(instance_name, None)
        _last_qr_at.pop(instance_name, None)
        delete_instance_key_record(instance_name)
        delete_all_instance_webhooks(instance_name)
        return {"ok": True, "instance": {"id": instance_name, "name": instance_name, "status": "close"}, "raw": result}
    except EvolutionError as exc:
        if exc.status_code == 404:
            logger.warning("delete_idempotent_fallback", instance=instance_name, error=str(exc))
            _last_known_state.pop(instance_name, None)
            _last_qr_at.pop(instance_name, None)
            delete_instance_key_record(instance_name)
            delete_all_instance_webhooks(instance_name)
            return {"ok": True, "instance": {"id": instance_name, "name": instance_name, "status": "close"}, "stale": True}
        raise _apply_http_error(exc)
    except Exception as exc:
        raise _apply_http_error(exc)


@router.get("/{instance_name}/api-key")
async def get_instance_api_key(instance_name: str, request: Request, reveal: bool = Query(default=False)):
    instance_name = _validate_instance_name(instance_name)
    auth_instance = getattr(request.state, "auth_instance", None)
    is_admin = bool(getattr(request.state, "is_admin", False))
    if auth_instance and auth_instance != instance_name:
        raise HTTPException(status_code=403, detail="Token no autorizado para esta instancia")
    if reveal and not is_admin:
        raise HTTPException(status_code=403, detail="Solo admin puede revelar API key completa")
    ensure_instance_key(instance_name, instance_id=instance_name)
    return get_instance_key_info(instance_name, reveal=reveal)


@router.post("/{instance_name}/api-key/regenerate")
async def regenerate_instance_api_key(instance_name: str, request: Request):
    instance_name = _validate_instance_name(instance_name)
    if not bool(getattr(request.state, "is_admin", False)):
        raise HTTPException(status_code=403, detail="Solo admin puede regenerar API key")
    return create_or_regenerate_instance_key(instance_name, instance_id=instance_name)


@router.delete("/{instance_name}/api-key")
async def revoke_api_key(instance_name: str, request: Request):
    instance_name = _validate_instance_name(instance_name)
    if not bool(getattr(request.state, "is_admin", False)):
        raise HTTPException(status_code=403, detail="Solo admin puede revocar API key")
    return revoke_instance_key(instance_name)


@router.post("/{instance_name}/api-key/enable")
async def enable_api_key(instance_name: str, request: Request):
    instance_name = _validate_instance_name(instance_name)
    if not bool(getattr(request.state, "is_admin", False)):
        raise HTTPException(status_code=403, detail="Solo admin puede habilitar API key")
    return create_or_regenerate_instance_key(instance_name, instance_id=instance_name)
