"""
Receptor de webhooks de Evolution API.
Evolution hace POST aca, el gateway lo procesa y lo reenvia al bot.
"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.instance_webhooks import (
    list_enabled_webhooks_for_dispatch,
    list_instance_webhooks,
)
from app.services.event_pipeline import process_incoming_webhook, snapshot_pipeline_metrics
from app.services.normalization import list_events, save_pipeline_event
from app.services.webhook_delivery import dispatch_webhook_with_retry
from app.services.webhook_delivery import diagnose_webhook_target
from app.services.evolution_auth import auth_runtime_snapshot, extract_evolution_auth, validate_evolution_auth

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
    dispatch_id = f"disp_{request_id}_{str(item.get('id') or '')[:6]}"
    dispatch_payload = {
        **payload,
        "dispatchId": dispatch_id,
    }
    save_pipeline_event(
        stage="dispatch_select_webhook",
        status="ok",
        instance=str(payload.get("instance") or ""),
        message_id=(payload.get("message") or {}).get("id"),
        conversation_id=(payload.get("meta") or {}).get("conversationId"),
        request_id=request_id,
        details={"webhookId": item.get("id"), "dispatchId": dispatch_id},
    )
    result = await dispatch_webhook_with_retry(payload=dispatch_payload, request_id=request_id, item=item)
    save_pipeline_event(
        stage="dispatch_result",
        status="ok" if result.get("ok") else str(result.get("status") or "failed"),
        instance=str(payload.get("instance") or ""),
        message_id=(payload.get("message") or {}).get("id"),
        conversation_id=(payload.get("meta") or {}).get("conversationId"),
        request_id=request_id,
        details={
            "webhookId": item.get("id"),
            "dispatchId": dispatch_id,
            "statusCode": result.get("statusCode"),
            "status": result.get("status"),
            "retriesUsed": result.get("retriesUsed"),
        },
    )
    if not result.get("ok"):
        save_pipeline_event(
            stage="dispatch",
            status="failed",
            instance=str(payload.get("instance") or ""),
            message_id=(payload.get("message") or {}).get("id"),
            conversation_id=(payload.get("meta") or {}).get("conversationId"),
            request_id=request_id,
            details={
                "webhookId": item.get("id"),
                "statusCode": result.get("statusCode"),
                "status": result.get("status"),
                "retriesUsed": result.get("retriesUsed"),
                "error": result.get("error"),
            },
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
                "eventFilters": {"business": True, "transport": False, "operational": False},
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
        logger.warning("bot_webhook_dispatch_skipped_no_target", request_id=request_id, instance=instance_name)
        return

    async with _forward_semaphore:
        results = await asyncio.gather(*[_dispatch_single_webhook(payload, request_id, hook) for hook in webhooks], return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                hook = webhooks[idx] if idx < len(webhooks) else {}
                logger.error(
                    "webhook_dispatch_worker_exception",
                    request_id=request_id,
                    instance=instance_name,
                    webhook_id=hook.get("id"),
                    error=str(result),
                )
                save_pipeline_event(
                    stage="dispatch_worker_exception",
                    status="error",
                    instance=instance_name,
                    message_id=(payload.get("message") or {}).get("id"),
                    conversation_id=(payload.get("meta") or {}).get("conversationId"),
                    request_id=request_id,
                    details={"webhookId": hook.get("id"), "error": str(result)[:220]},
                )


def _resolve_public_gateway_base_url() -> str:
    configured = (settings.public_base_url or "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://127.0.0.1:{settings.gateway_port}".rstrip("/")


def _with_gateway_media_url(normalized: dict[str, Any]) -> dict[str, Any] | None:
    media = normalized.get("media")
    if not isinstance(media, dict):
        return media if isinstance(media, dict) else None
    media_id = str(media.get("id") or "").strip()
    instance = str(normalized.get("instance") or "").strip()
    if not media_id or not instance:
        return dict(media)
    gateway_media_url = f"{_resolve_public_gateway_base_url()}/instances/{instance}/media/{media_id}"
    return {
        **media,
        "gateway_media_url": gateway_media_url,
        "gatewayMediaUrl": gateway_media_url,
        "proxy_url": gateway_media_url,
        "proxyUrl": gateway_media_url,
    }


def _extract_contact_name_for_bot(normalized: dict[str, Any]) -> str | None:
    message = normalized.get("message") if isinstance(normalized.get("message"), dict) else {}
    context = normalized.get("context") if isinstance(normalized.get("context"), dict) else {}
    chat = context.get("chat") if isinstance(context.get("chat"), dict) else {}
    raw_payload = normalized.get("raw") if isinstance(normalized.get("raw"), dict) else {}
    raw_data = raw_payload.get("data") if isinstance(raw_payload.get("data"), dict) else {}

    candidates = [
        message.get("pushName"),
        chat.get("participantName"),
        raw_data.get("pushName"),
        normalized.get("contactName"),
        normalized.get("pushName"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            cleaned = " ".join(candidate.split()).strip()
            if cleaned:
                return cleaned
    return None


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

    contact_name = _extract_contact_name_for_bot(normalized)

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
        "contactName": contact_name,
        "pushName": contact_name,
        "text": normalized.get("text"),
        "content": normalized.get("content"),
        "message": normalized.get("message"),
        "media": _with_gateway_media_url(normalized),
        "status": normalized.get("status"),
        "metadata": normalized.get("metadata"),
        "context": normalized.get("context"),
        "interaction": normalized.get("interaction"),
        "eventType": normalized.get("eventType"),
        "category": normalized.get("category"),
        "transport": normalized.get("transport"),
        "operational": normalized.get("operational"),
        "raw": normalized.get("raw"),
        "meta": normalized.get("meta"),
        "trace": {
            "requestId": (normalized.get("meta") or {}).get("requestId"),
            "conversationId": (normalized.get("meta") or {}).get("conversationId"),
            "messageId": (normalized.get("message") or {}).get("id"),
            "instance": normalized.get("instance"),
            "retryAttempt": 0,
        },
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
    request_id = str(uuid.uuid4())[:12]
    instance = str(payload.get("instance", "unknown"))
    source_ip = request.client.host if request.client else "unknown"
    header_presence = {
        "apikey": bool(request.headers.get("apikey")),
        "x_api_key": bool(request.headers.get("x-api-key")),
        "authorization": bool(request.headers.get("authorization")),
    }
    logger.info(
        "evolution_webhook_received",
        request_id=request_id,
        instance=instance,
        source_ip=source_ip,
        source_event=payload.get("event"),
        header_presence=header_presence,
    )

    extracted = extract_evolution_auth(request.headers, payload)
    provided_key = extracted["providedKey"]
    provided_source = extracted["source"]
    logger.info(
        "evolution_auth_extracted",
        request_id=request_id,
        instance=instance,
        auth_source=provided_source,
        received_prefix=provided_key[:8] if provided_key else "",
        normalized=extracted["normalized"],
    )

    auth_validation = await validate_evolution_auth(payload, provided_key)
    logger.info(
        "evolution_auth_validation",
        request_id=request_id,
        instance=instance,
        auth_source=provided_source,
        expected_global_prefix=auth_validation["expectedGlobalPrefix"],
        expected_instance_prefix=auth_validation["expectedInstancePrefix"],
        received_prefix=auth_validation["receivedPrefix"],
        comparison_mode="global_or_instance",
        result="ok" if auth_validation["accepted"] else "fail",
        accepted_mode=auth_validation["mode"],
    )
    save_pipeline_event(
        stage="evolution_auth",
        status="ok" if auth_validation["accepted"] else "fail",
        instance=instance,
        request_id=request_id,
        event=str(payload.get("event") or "UNKNOWN"),
        details={
            "source": provided_source,
            "receivedPrefix": auth_validation["receivedPrefix"],
            "expectedGlobalPrefix": auth_validation["expectedGlobalPrefix"],
            "expectedInstancePrefix": auth_validation["expectedInstancePrefix"],
            "acceptedMode": auth_validation["mode"],
        },
    )

    if not auth_validation["accepted"]:
        if settings.allow_insecure_evolution_webhooks:
            logger.warning(
                "evolution_webhook_auth_insecure_bypass",
                request_id=request_id,
                instance=instance,
                source=provided_source,
                received_prefix=auth_validation["receivedPrefix"],
            )
            save_pipeline_event(
                stage="evolution_auth",
                status="insecure_bypass",
                instance=instance,
                request_id=request_id,
                details={"source": provided_source, "receivedPrefix": auth_validation["receivedPrefix"]},
            )
        elif not provided_key:
            logger.warning("evolution_webhook_auth_missing", instance=instance, source=provided_source)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook auth missing")
        else:
            logger.warning(
                "evolution_webhook_auth_failed",
                instance=instance,
                source=provided_source,
                received_prefix=auth_validation["receivedPrefix"],
                expected_global_prefix=auth_validation["expectedGlobalPrefix"],
                expected_instance_prefix=auth_validation["expectedInstancePrefix"],
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook auth failed")
    else:
        logger.info("evolution_webhook_auth_success", instance=instance, source=provided_source, mode=auth_validation["mode"])

    logger.debug("webhook_received", request_id=request_id, source_event=payload.get("event"), instance=instance)
    logger.info("[OUTBOUND][WEBHOOK] evolution webhook received", request_id=request_id, instance=instance, source_event=payload.get("event"))
    pipeline_result = process_incoming_webhook(payload, request_id)
    normalized = pipeline_result.get("normalized") or {}
    event = str(normalized.get("event") or payload.get("event") or "UNKNOWN")

    if pipeline_result.get("status") == "ignored_technical":
        logger.debug(
            "webhook_technical_ignored",
            request_id=request_id,
            source_event=normalized.get("sourceEvent") or payload.get("event"),
            instance=instance,
            reason=normalized.get("reason"),
        )
        return {"status": "ignored_technical"}
    if pipeline_result.get("status") == "normalize_error":
        logger.error("webhook_normalize_error", request_id=request_id, instance=instance)
        return {"status": "normalize_error"}
    if pipeline_result.get("status") == "stale_dropped":
        return {"status": "stale_dropped"}
    if pipeline_result.get("status") == "ignored_group":
        return {"status": "ignored_group"}
    if pipeline_result.get("status") in {"duplicate", "duplicate_fp", "echo_filtered", "throttled"}:
        return {"status": pipeline_result.get("status")}

    message = normalized.get("message") or {}
    msg_id = str(message.get("id") or "")
    conv_id = str((normalized.get("meta") or {}).get("conversationId") or "")

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
        interaction_type=((normalized.get("interaction") or {}).get("interactionType")),
        selected_id=((normalized.get("interaction") or {}).get("id")),
        selected_title=((normalized.get("interaction") or {}).get("title")),
        rich_subtype=normalized.get("subtype"),
        has_location=bool(((normalized.get("metadata") or {}).get("hasLocation"))),
        has_contacts=bool(((normalized.get("metadata") or {}).get("hasContacts"))),
        has_poll=bool(((normalized.get("metadata") or {}).get("hasPoll"))),
        reaction_emoji=((normalized.get("content") or {}).get("emoji")) if isinstance(normalized.get("content"), dict) else None,
        poll_option_count=((normalized.get("metadata") or {}).get("pollOptionCount")),
        special_flags={
            "ephemeral": (normalized.get("metadata") or {}).get("ephemeral"),
            "edited": (normalized.get("metadata") or {}).get("edited"),
            "revoked": (normalized.get("metadata") or {}).get("revoked"),
            "fromBusiness": (normalized.get("metadata") or {}).get("fromBusiness"),
            "newsletter": (normalized.get("metadata") or {}).get("newsletter"),
            "statusMessage": (normalized.get("metadata") or {}).get("statusMessage"),
        },
        pipeline_trace=(normalized.get("operational") or {}).get("pipeline"),
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
            status="forwarding_from_me",
            instance=instance,
            message_id=msg_id,
            conversation_id=conv_id,
            request_id=request_id,
        )
        logger.info(
            "from_me_forwarded_to_bot",
            request_id=request_id,
            instance=instance,
            message_id=msg_id or None,
            conversation_id=conv_id or None,
            direction=normalized.get("direction"),
            subtype=normalized.get("subtype"),
        )

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
    logger.info(
        "[CHAT][EMIT] queued forward event",
        request_id=request_id,
        instance=instance,
        message_id=msg_id or None,
        conversation_id=conv_id or None,
        direction=normalized.get("direction"),
        subtype=normalized.get("subtype"),
    )
    save_pipeline_event(
        stage="dispatch",
        status="queued",
        instance=instance,
        message_id=msg_id or None,
        conversation_id=conv_id or None,
        request_id=request_id,
        details={"queueSize": len(_forward_tasks)},
    )

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


@router.get("/pipeline-metrics")
async def get_pipeline_metrics():
    return snapshot_pipeline_metrics()


@router.get("/dispatch-metrics")
async def get_dispatch_metrics(instance: str | None = None):
    if instance:
        items = list_instance_webhooks(instance, reveal_secrets=False)
        return {"instance": instance, "webhooks": items}
    events = list_events(instance=None, limit=300)
    by_instance: dict[str, dict[str, Any]] = {}
    for event in events:
        inst = str(event.get("instance") or "unknown")
        if inst not in by_instance:
            by_instance[inst] = {"events": 0, "dispatchFailures": 0, "dispatchQueued": 0}
        by_instance[inst]["events"] += 1
        pipeline = event.get("pipeline") if isinstance(event.get("pipeline"), dict) else {}
        stage = str(pipeline.get("stage") or "")
        status = str(pipeline.get("status") or "")
        if stage == "dispatch" and status == "failed":
            by_instance[inst]["dispatchFailures"] += 1
        if stage == "dispatch" and status == "queued":
            by_instance[inst]["dispatchQueued"] += 1
    return {"instances": by_instance}


@router.get("/debug/webhook-connectivity")
async def debug_webhook_connectivity(url: str = "http://host.docker.internal:8000/"):
    logger.info("webhook_connectivity_debug_start", url=url)
    result = await diagnose_webhook_target(url=url, timeout_s=8.0)
    logger.info(
        "webhook_connectivity_debug_result",
        url=url,
        dns_ok=result.get("dns", {}).get("resolved"),
        tcp_ok=result.get("tcp", {}).get("ok"),
        http_ok=result.get("http", {}).get("ok"),
        http_status=result.get("http", {}).get("statusCode"),
    )
    return result


@router.get("/evolution-auth/runtime")
async def get_evolution_auth_runtime():
    return await auth_runtime_snapshot()
