import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.requests import WebhookConfigRequest, WebhookEnabledRequest
from app.services.instance_webhooks import (
    build_auth_headers,
    create_webhook,
    delete_webhook,
    get_webhook,
    list_instance_webhooks,
    mark_dispatch_result,
    mask_headers_for_log,
    set_webhook_enabled,
    update_webhook,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/instances/{instance_name}/webhooks", tags=["instance-webhooks"])


def _validate_instance_name(instance_name: str) -> str:
    cleaned = str(instance_name or "").strip()
    if not cleaned or not all(ch.islower() or ch.isdigit() or ch == "_" for ch in cleaned):
        raise HTTPException(status_code=400, detail="Nombre de instancia invalido")
    return cleaned


def _validate_url(url: str) -> str:
    value = str(url or "").strip()
    if not value.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL invalida: debe iniciar con http:// o https://")
    return value


def _check_instance_scope(request: Request, instance_name: str) -> None:
    auth_instance = getattr(request.state, "auth_instance", None)
    if auth_instance and auth_instance != instance_name:
        raise HTTPException(status_code=403, detail="Token no autorizado para esta instancia")


@router.get("")
@router.get("/", include_in_schema=False)
async def list_webhooks(instance_name: str, request: Request):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    reveal = bool(getattr(request.state, "is_admin", False))
    return {"items": list_instance_webhooks(name, reveal_secrets=reveal)}


@router.post("")
@router.post("/", include_in_schema=False)
async def create_webhook_route(instance_name: str, request: Request, body: WebhookConfigRequest):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = create_webhook(
        name,
        url=_validate_url(body.url),
        enabled=body.enabled,
        auth_type=body.authType,
        auth_config=body.authConfig,
        custom_headers=body.customHeaders,
    )
    logger.info("webhook_create", instance=name, webhook_id=item["id"], auth_type=item["authType"])
    return item


@router.put("/{webhook_id}")
async def update_webhook_route(instance_name: str, webhook_id: str, request: Request, body: WebhookConfigRequest):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = update_webhook(
        name,
        webhook_id,
        url=_validate_url(body.url),
        enabled=body.enabled,
        auth_type=body.authType,
        auth_config=body.authConfig,
        custom_headers=body.customHeaders,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    logger.info("webhook_update", instance=name, webhook_id=webhook_id, auth_type=item["authType"], enabled=item["enabled"])
    return item


@router.patch("/{webhook_id}/enabled")
async def set_enabled_route(instance_name: str, webhook_id: str, request: Request, body: WebhookEnabledRequest):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = set_webhook_enabled(name, webhook_id, enabled=body.enabled)
    if not item:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    return item


@router.delete("/{webhook_id}")
async def delete_webhook_route(instance_name: str, webhook_id: str, request: Request):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    ok = delete_webhook(name, webhook_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    logger.info("webhook_delete", instance=name, webhook_id=webhook_id)
    return {"ok": True}


@router.post("/{webhook_id}/test")
async def test_webhook_route(instance_name: str, webhook_id: str, request: Request):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = get_webhook(name, webhook_id, reveal_secrets=True)
    if not item:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    if not item.get("enabled"):
        raise HTTPException(status_code=400, detail="Webhook deshabilitado")
    logger.info("webhook_test", instance=name, webhook_id=webhook_id, phase="start")

    url = _validate_url(str(item.get("url") or ""))
    payload: dict[str, Any] = {
        "id": "test_webhook",
        "event": "TEST_WEBHOOK",
        "instance": name,
        "timestamp": int(time.time() * 1000),
        "layer": "business",
        "type": "message",
        "messageType": "text",
        "sender": "test@botly",
        "recipient": name,
        "text": "test webhook",
        "content": "test webhook",
        "status": "received",
        "message": {"id": "test-msg", "kind": "text", "from": "test@botly", "text": "test webhook"},
        "meta": {"source": "manual_test"},
    }

    headers = {"Content-Type": "application/json", **build_auth_headers(item)}
    logger.info(
        "webhook_dispatch_start",
        instance=name,
        webhook_id=webhook_id,
        url=url,
        headers=mask_headers_for_log(headers),
        test_mode=True,
    )

    settings = get_settings()
    timeout = httpx.Timeout(
        connect=min(5.0, float(settings.instance_webhook_timeout)),
        read=float(settings.instance_webhook_timeout),
        write=min(8.0, float(settings.instance_webhook_timeout)),
        pool=2.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code < 200 or resp.status_code >= 300:
            text = (resp.text or "")[:220]
            status_value = f"http_{resp.status_code}"
            mark_dispatch_result(name, webhook_id, status=status_value, error=text)
            logger.warning(
                "webhook_dispatch_non_2xx",
                instance=name,
                webhook_id=webhook_id,
                status_code=resp.status_code,
                error=text,
                test_mode=True,
            )
            return {"ok": False, "status": resp.status_code, "error": text}

        mark_dispatch_result(name, webhook_id, status=f"ok_{resp.status_code}", error=None)
        logger.info("webhook_dispatch_success", instance=name, webhook_id=webhook_id, status_code=resp.status_code, test_mode=True)
        logger.info("webhook_test", instance=name, webhook_id=webhook_id, phase="done", ok=True, status=resp.status_code)
        return {"ok": True, "status": resp.status_code}
    except httpx.TimeoutException as exc:
        mark_dispatch_result(name, webhook_id, status="timeout", error=str(exc))
        logger.warning("webhook_dispatch_timeout", instance=name, webhook_id=webhook_id, error=str(exc), test_mode=True)
        logger.warning("webhook_test", instance=name, webhook_id=webhook_id, phase="done", ok=False, status=504, error="timeout")
        return {"ok": False, "status": 504, "error": "timeout"}
    except Exception as exc:
        mark_dispatch_result(name, webhook_id, status="connection_fail", error=str(exc))
        logger.error("webhook_dispatch_connection_fail", instance=name, webhook_id=webhook_id, error=str(exc), test_mode=True)
        logger.error("webhook_test", instance=name, webhook_id=webhook_id, phase="done", ok=False, status=502, error=str(exc))
        return {"ok": False, "status": 502, "error": str(exc)}
