"""
Receptor de webhooks de Evolution API.
Evolution hace POST aca, el gateway lo procesa y lo reenvia al bot.
"""

import asyncio
import time
import uuid
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.instance_webhooks import (
    build_auth_headers,
    list_enabled_webhooks_for_dispatch,
    mark_dispatch_result,
    mask_headers_for_log,
)
from app.services.normalization import list_events, normalize_webhook, save_event, save_pipeline_event, save_raw_event
from app.services.reliability import (
    conversation_id,
    inbound_dedupe,
    is_flood,
    looks_like_outbound_echo,
    message_fingerprint,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
settings = get_settings()

_forward_semaphore = asyncio.Semaphore(max(1, settings.bot_webhook_max_parallel))
_forward_tasks: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> None:
    _forward_tasks.add(task)

    def _cleanup(done: asyncio.Task) -> None:
        _forward_tasks.discard(done)

    task.add_done_callback(_cleanup)


async def shutdown_forward_workers(timeout_s: float = 3.0) -> None:
    if not _forward_tasks:
        return
    to_cancel = list(_forward_tasks)
    for task in to_cancel:
        if not task.done():
            task.cancel()
    try:
        await asyncio.wait(to_cancel, timeout=timeout_s)
    except Exception:
        return


async def _dispatch_single_webhook(payload: dict[str, Any], request_id: str, item: dict[str, Any]) -> None:
    instance_name = str(payload.get("instance") or "")
    webhook_id = str(item.get("id") or "")
    url = str(item.get("url") or "")
    headers = {"Content-Type": "application/json", **build_auth_headers(item)}
    start = time.perf_counter()

    logger.info(
        "webhook_dispatch_start",
        request_id=request_id,
        instance=instance_name,
        webhook_id=webhook_id,
        url=url,
        headers=mask_headers_for_log(headers),
    )

    timeout = httpx.Timeout(
        connect=min(5.0, float(settings.instance_webhook_timeout)),
        read=float(settings.instance_webhook_timeout),
        write=min(8.0, float(settings.instance_webhook_timeout)),
        pool=2.0,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        if resp.status_code < 200 or resp.status_code >= 300:
            err = (resp.text or "")[:220]
            status_value = f"http_{resp.status_code}"
            mark_dispatch_result(instance_name, webhook_id, status=status_value, error=err)
            logger.warning(
                "webhook_dispatch_non_2xx",
                request_id=request_id,
                instance=instance_name,
                webhook_id=webhook_id,
                status=resp.status_code,
                latency_ms=latency_ms,
                error=err,
            )
            return

        mark_dispatch_result(instance_name, webhook_id, status=f"ok_{resp.status_code}", error=None)
        logger.info(
            "webhook_dispatch_success",
            request_id=request_id,
            instance=instance_name,
            webhook_id=webhook_id,
            status=resp.status_code,
            latency_ms=latency_ms,
        )
    except httpx.TimeoutException as exc:
        mark_dispatch_result(instance_name, webhook_id, status="timeout", error=str(exc))
        logger.warning(
            "webhook_dispatch_timeout",
            request_id=request_id,
            instance=instance_name,
            webhook_id=webhook_id,
            error=str(exc),
        )
    except Exception as exc:
        mark_dispatch_result(instance_name, webhook_id, status="connection_fail", error=str(exc))
        logger.error(
            "webhook_dispatch_connection_fail",
            request_id=request_id,
            instance=instance_name,
            webhook_id=webhook_id,
            error=str(exc),
        )


async def _forward_to_instance_webhooks(payload: dict[str, Any], request_id: str) -> None:
    instance_name = str(payload.get("instance") or "")
    webhooks = list_enabled_webhooks_for_dispatch(instance_name)
    if not webhooks and settings.bot_webhook_url:
        webhooks = [
            {
                "id": "legacy_default",
                "url": settings.bot_webhook_url,
                "authType": "NONE",
                "authConfig": {},
                "customHeaders": {},
                "enabled": True,
            }
        ]

    if not webhooks:
        save_pipeline_event(
            stage="forward_to_instance_webhooks",
            status="skipped_no_target",
            instance=payload.get("instance"),
            message_id=(payload.get("message") or {}).get("id"),
            conversation_id=(payload.get("meta") or {}).get("conversationId"),
            request_id=request_id,
        )
        logger.warning("webhook_dispatch_skipped_no_target", request_id=request_id, instance=instance_name)
        return

    async with _forward_semaphore:
        await asyncio.gather(*[_dispatch_single_webhook(payload, request_id, hook) for hook in webhooks], return_exceptions=True)


def _to_bot_payload(normalized: dict[str, Any]) -> dict[str, Any] | None:
    if normalized.get("layer") != "business":
        return None

    normalized_type = str(normalized.get("type") or "")
    subtype = str(normalized.get("subtype") or normalized.get("messageType") or "")
    if normalized_type == "message":
        pass
    elif normalized_type == "event" and subtype == "message_status":
        pass
    else:
        return None

    return {
        "id": normalized.get("id"),
        "type": normalized_type,
        "subtype": subtype,
        "originalType": normalized.get("originalType"),
        "instance": normalized.get("instance"),
        "timestamp": normalized.get("timestamp"),
        "direction": normalized.get("direction"),
        "messageType": subtype,
        "messageId": normalized.get("messageId"),
        "sender": normalized.get("sender"),
        "recipient": normalized.get("recipient"),
        "text": normalized.get("text"),
        "content": normalized.get("content"),
        "media": normalized.get("media"),
        "status": normalized.get("status"),
        "metadata": normalized.get("metadata"),
        "context": normalized.get("context"),
        "raw": normalized.get("raw"),
        "meta": normalized.get("meta"),
    }


@router.post("/evolution")
async def receive_webhook(request: Request):
    """
    Endpoint que recibe todos los eventos de Evolution.
    Responde 200 inmediatamente y procesa de forma asincrona.
    """

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body JSON invalido")

    expected_key = (settings.evolution_api_key or "").strip()
    header_candidates = [
        request.headers.get("apikey"),
        request.headers.get("x-api-key"),
        request.headers.get("authorization"),
    ]
    provided_key = ""
    provided_source = "none"
    for idx, candidate in enumerate(header_candidates):
        if not candidate:
            continue
        raw = str(candidate).strip()
        value = raw
        if idx == 2:
            value = re.sub(r"^Bearer\s+", "", raw, flags=re.IGNORECASE).strip()
        if value:
            provided_key = value
            provided_source = ("apikey", "x-api-key", "authorization")[idx]
            break
    if not provided_key:
        body_key = str(payload.get("apikey") or "").strip()
        if body_key:
            provided_key = body_key
            provided_source = "payload.apikey"

    if expected_key and not provided_key:
        logger.warning("webhook_auth_missing", instance=payload.get("instance"), source=provided_source)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook auth missing")

    if expected_key and provided_key != expected_key:
        logger.warning(
            "webhook_auth_failed",
            instance=payload.get("instance"),
            source=provided_source,
            received_prefix=provided_key[:8],
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook auth failed")

    if expected_key:
        logger.info("webhook_auth_success", instance=payload.get("instance"), source=provided_source)
    else:
        logger.warning("webhook_auth_disabled_missing_expected_key", instance=payload.get("instance"))

    raw_event = str(payload.get("event", "UNKNOWN"))
    instance = str(payload.get("instance", "unknown"))
    request_id = str(uuid.uuid4())[:12]

    logger.debug("webhook_received", request_id=request_id, source_event=raw_event, instance=instance)

    normalized = normalize_webhook(payload)
    event = str(normalized.get("event") or raw_event)
    save_raw_event({"requestId": request_id, "payload": payload, "normalized": normalized, "timestamp": int(time.time() * 1000)})
    if normalized.get("layer") == "technical":
        logger.debug(
            "webhook_technical_ignored",
            request_id=request_id,
            source_event=normalized.get("sourceEvent") or raw_event,
            instance=instance,
            reason=normalized.get("reason"),
        )
        return {"status": "ignored_technical"}

    save_pipeline_event(
        stage="webhook_received",
        status="ok",
        instance=instance,
        request_id=request_id,
        event=event,
    )

    message = normalized.get("message") or {}
    msg_id = str(message.get("id") or "")
    conv_id = conversation_id(instance, message.get("from"))
    normalized["meta"] = {"requestId": request_id, "conversationId": conv_id}

    if event in {"MESSAGES_UPSERT", "SEND_MESSAGE"}:
        if msg_id and inbound_dedupe.exists(msg_id):
            save_pipeline_event(
                stage="dedupe",
                status="skipped_duplicate_id",
                instance=instance,
                message_id=msg_id,
                conversation_id=conv_id,
                request_id=request_id,
            )
            logger.warning("duplicate_message_ignored", request_id=request_id, instance=instance, message_id=msg_id)
            return {"status": "duplicate"}
        if msg_id:
            inbound_dedupe.put(msg_id)

        fp = message_fingerprint(
            instance=instance,
            remote_jid=message.get("from"),
            kind=message.get("kind"),
            text=message.get("text"),
            media_id=(normalized.get("media") or {}).get("id") if normalized.get("media") else None,
        )
        if inbound_dedupe.exists(fp):
            save_pipeline_event(
                stage="dedupe",
                status="skipped_duplicate_fingerprint",
                instance=instance,
                message_id=msg_id,
                conversation_id=conv_id,
                request_id=request_id,
            )
            logger.warning("duplicate_fingerprint_ignored", request_id=request_id, instance=instance, message_id=msg_id)
            return {"status": "duplicate_fp"}
        inbound_dedupe.put(fp)

        is_from_me = bool(message.get("fromMe"))
        if is_from_me:
            normalized["status"] = "sent"
            normalized["forwarding"] = {"status": "not_forwarded_from_me"}
            normalized["fromBot"] = False

        payload_text = str(message.get("text") or (normalized.get("media") or {}).get("caption") or "")
        if (not is_from_me) and looks_like_outbound_echo(
            instance, message.get("from"), str(message.get("kind") or "unknown"), payload_text
        ):
            save_pipeline_event(
                stage="anti_loop",
                status="skipped_outbound_echo",
                instance=instance,
                message_id=msg_id,
                conversation_id=conv_id,
                request_id=request_id,
            )
            logger.warning("outbound_echo_ignored", request_id=request_id, instance=instance, message_id=msg_id)
            return {"status": "echo_filtered"}

        flooded, count = is_flood(conv_id)
        if flooded:
            save_pipeline_event(
                stage="flood_guard",
                status="throttled",
                instance=instance,
                message_id=msg_id,
                conversation_id=conv_id,
                request_id=request_id,
                details={"messagesInWindow": count},
            )
            logger.warning("conversation_throttled", request_id=request_id, instance=instance, message_id=msg_id, messages_in_window=count)
            return {"status": "throttled"}

    save_event(normalized)
    save_pipeline_event(
        stage="normalized",
        status="ok",
        instance=instance,
        message_id=msg_id or None,
        conversation_id=conv_id,
        request_id=request_id,
    )

    normalized_kind = (normalized.get("message") or {}).get("kind")
    logger.info(
        "webhook_normalized",
        request_id=request_id,
        source_event=normalized.get("event"),
        instance=normalized.get("instance"),
        normalized_type=normalized.get("type"),
        normalized_subtype=normalized.get("subtype") or normalized_kind,
        original_type=normalized.get("originalType"),
        fallback_used=bool((normalized.get("metadata") or {}).get("unknownTypeDetected")),
        has_media=bool(normalized.get("media")),
        has_context=bool(normalized.get("context")),
        has_quoted=bool(((normalized.get("context") or {}).get("quoted"))),
        has_mentions=bool(((normalized.get("context") or {}).get("mentions"))),
        is_forwarded=bool((normalized.get("metadata") or {}).get("forwarded")),
        is_group=bool((((normalized.get("context") or {}).get("chat")) or {}).get("isGroup")),
        quoted_type=(((normalized.get("context") or {}).get("quoted")) or {}).get("type"),
        quoted_preview=(((normalized.get("context") or {}).get("quoted")) or {}).get("preview"),
        unknown_type_detected=bool((normalized.get("metadata") or {}).get("unknownTypeDetected")),
    )

    bot_payload = _to_bot_payload(normalized)
    if not bot_payload:
        logger.debug(
            "webhook_not_forwarded_non_business",
            request_id=request_id,
            instance=instance,
            layer=normalized.get("layer"),
            event_type=normalized.get("type"),
            message_type=normalized.get("messageType"),
        )
        return {"status": "not_forwarded"}

    if bool((normalized.get("message") or {}).get("fromMe")):
        save_pipeline_event(
            stage="anti_loop",
            status="skipped_from_me",
            instance=instance,
            message_id=msg_id,
            conversation_id=conv_id,
            request_id=request_id,
        )
        logger.debug("loop_prevented_from_me", request_id=request_id, instance=instance, message_id=msg_id)
        return {"status": "from_me_saved"}

    if len(_forward_tasks) >= settings.bot_webhook_max_queue:
        save_pipeline_event(
            stage="forward_to_instance_webhooks",
            status="dropped_queue_full",
            instance=instance,
            message_id=msg_id or None,
            conversation_id=conv_id,
            request_id=request_id,
            details={"queueSize": len(_forward_tasks)},
        )
        logger.error("webhook_dispatch_dropped_queue_full", queue_size=len(_forward_tasks))
        return {"status": "queued_dropped"}

    task = asyncio.create_task(_forward_to_instance_webhooks(bot_payload, request_id))
    _track_background_task(task)

    return {"status": "ok"}


@router.get("/events")
async def get_events(request: Request, instance: str | None = None, limit: int = 100):
    auth_instance = getattr(request.state, "auth_instance", None)
    if auth_instance:
        if not instance:
            raise HTTPException(status_code=403, detail="Token de instancia requiere filtro ?instance=")
        if instance != auth_instance:
            raise HTTPException(status_code=403, detail="Token no autorizado para esta instancia")
    safe_limit = max(1, min(limit, 500))
    return {"items": list_events(instance=instance, limit=safe_limit)}
