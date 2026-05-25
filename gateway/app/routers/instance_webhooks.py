import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.models.requests import WebhookConfigRequest, WebhookEnabledRequest
from app.services.instance_webhooks import (
    build_auth_headers,
    create_webhook,
    delete_webhook,
    get_webhook,
    list_instance_webhooks,
    mask_headers_for_log,
    set_webhook_enabled,
    set_webhook_filters,
    update_webhook,
)
from app.services.webhook_delivery import dispatch_webhook_with_retry
from app.services.webhook_delivery import diagnose_webhook_target

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
        event_filters=body.eventFilters,
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
        event_filters=body.eventFilters,
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


@router.patch("/{webhook_id}/filters")
async def set_filters_route(instance_name: str, webhook_id: str, request: Request, body: dict[str, bool]):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = set_webhook_filters(name, webhook_id, body)
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
    logger.info("[WEBHOOK_TEST][START] webhook test requested", instance=name, webhook_id=webhook_id)
    _check_instance_scope(request, name)

    try:
        item = get_webhook(name, webhook_id, reveal_secrets=True)
        logger.info(
            "[WEBHOOK_TEST][LOAD] webhook loaded",
            instance=name,
            webhook_id=webhook_id,
            found=bool(item),
        )
        if not item:
            raise HTTPException(status_code=404, detail="Webhook no encontrado")
        if not item.get("enabled"):
            raise HTTPException(status_code=400, detail="Webhook deshabilitado")

        logger.info(
            "[WEBHOOK_TEST][AUTH] webhook auth metadata",
            instance=name,
            webhook_id=webhook_id,
            auth_type=item.get("authType"),
            has_auth_config=bool(item.get("authConfig")),
            has_custom_headers=bool(item.get("customHeaders")),
        )

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
            "category": "business_message",
        }
        logger.info(
            "[WEBHOOK_TEST][PAYLOAD] payload ready",
            instance=name,
            webhook_id=webhook_id,
            url=url,
            payload_event=payload.get("event"),
            payload_type=payload.get("type"),
        )

        logger.info("[WEBHOOK_TEST][SEND] dispatch start", instance=name, webhook_id=webhook_id, url=url)
        result = await dispatch_webhook_with_retry(
            payload=payload,
            request_id=f"test-{webhook_id[:6]}",
            item=item,
            test_mode=True,
        )
        logger.info(
            "[WEBHOOK_TEST][RESPONSE] dispatch finished",
            instance=name,
            webhook_id=webhook_id,
            ok=bool(result.get("ok")),
            status_code=result.get("statusCode"),
            status=result.get("status"),
            retries_used=result.get("retriesUsed"),
            latency_ms=result.get("latencyMs"),
        )
        return {
            "ok": bool(result.get("ok")),
            "status": int(result.get("statusCode") or 0),
            "error": result.get("error"),
            "retriesUsed": int(result.get("retriesUsed") or 0),
            "latencyMs": float(result.get("latencyMs") or 0.0),
            "dispatchStatus": result.get("status"),
            "request": {
                "payload": payload,
                "headers": {
                    "authType": item.get("authType"),
                    "masked": mask_headers_for_log({"Content-Type": "application/json", **build_auth_headers(item)}),
                },
                "url": url,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[WEBHOOK_TEST][ERROR] unhandled exception during webhook test",
            instance=name,
            webhook_id=webhook_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "WEBHOOK_TEST_INTERNAL_ERROR",
                "message": "Fallo interno ejecutando test de webhook",
                "error": str(exc),
            },
        ) from exc


@router.get("/{webhook_id}/dispatches")
async def list_dispatches_route(instance_name: str, webhook_id: str, request: Request, limit: int = 20):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = get_webhook(name, webhook_id, reveal_secrets=False)
    if not item:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    history = item.get("dispatchHistory") if isinstance(item.get("dispatchHistory"), list) else []
    safe_limit = max(1, min(limit, 100))
    return {"items": history[:safe_limit]}


@router.post("/{webhook_id}/diagnose")
async def diagnose_webhook_route(instance_name: str, webhook_id: str, request: Request):
    name = _validate_instance_name(instance_name)
    _check_instance_scope(request, name)
    item = get_webhook(name, webhook_id, reveal_secrets=True)
    if not item:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    url = _validate_url(str(item.get("url") or ""))
    logger.info("webhook_network_diagnose_start", instance=name, webhook_id=webhook_id, url=url)
    result = await diagnose_webhook_target(url=url, timeout_s=8.0)
    logger.info(
        "webhook_network_diagnose_result",
        instance=name,
        webhook_id=webhook_id,
        dns_ok=result.get("dns", {}).get("resolved"),
        tcp_ok=result.get("tcp", {}).get("ok"),
        http_ok=result.get("http", {}).get("ok"),
        http_status=result.get("http", {}).get("statusCode"),
    )
    return result
