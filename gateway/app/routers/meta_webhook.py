"""Official Meta WhatsApp Cloud API webhook.

Meta verifies this route with GET and sends signed notifications to the same URL
with POST.  Provider payloads are adapted to the existing Gateway event pipeline
so Cloud and Evolution-originated events have one downstream contract.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.routers.webhooks import _to_bot_payload, _forward_to_instance_webhooks, _track_background_task, _forward_tasks
from app.services.audit import audit_event
from app.services.credential_manager import get_credential_manager
from app.services.event_pipeline import process_incoming_webhook
from app.services.core_inbound_dispatcher import CoreInboundPersistenceError, get_core_inbound_dispatcher
from app.services.instagram_webhook import InstagramWebhookError, process_instagram_webhook
from app.services.normalization import save_event, save_pipeline_event

router = APIRouter(prefix="/webhooks", tags=["meta-webhook"])
logger = get_logger(__name__)


def _instagram_timeline_event(canonical: dict[str, object]) -> dict[str, object] | None:
    """Project a canonical Instagram message into Gateway observability.

    This is deliberately a second, local projection: Core handoff remains
    owned by the durable dispatcher below.  Only the canonical, already
    sanitized fields are copied, so a provider body, token, or phone/JID never
    becomes part of the connection message timeline.
    """
    if canonical.get("eventType") != "message.created":
        return None
    transport = canonical.get("transport") if isinstance(canonical.get("transport"), dict) else {}
    message = canonical.get("message") if isinstance(canonical.get("message"), dict) else {}
    trace = canonical.get("trace") if isinstance(canonical.get("trace"), dict) else {}
    connection_id = str(transport.get("connectionRef") or "").strip()
    if not connection_id:
        return None
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    recipient = message.get("recipient") if isinstance(message.get("recipient"), dict) else {}
    attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
    attachment = next((item for item in attachments if isinstance(item, dict)), None)
    provider_message_id = str(message.get("providerMessageId") or "").strip() or None
    occurred_at = str(canonical.get("occurredAt") or "")
    try:
        timestamp = int(datetime.fromisoformat(occurred_at.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        timestamp = int(time.time() * 1000)
    media = None
    if attachment:
        media = {
            "id": attachment.get("providerMediaId"), "kind": attachment.get("kind"),
            "mimeType": attachment.get("mimeType"), "fileName": attachment.get("fileName"),
        }
    return {
        "id": str(canonical.get("eventId") or ""), "event": "INSTAGRAM_INBOUND_MESSAGE", "layer": "business",
        "instance": connection_id, "timestamp": timestamp, "type": "message", "subtype": message.get("kind") or "text",
        "messageType": message.get("kind") or "text", "direction": "inbound", "status": "received",
        "text": message.get("content") or "", "content": {"text": message.get("content") or ""},
        "sender": sender.get("externalId"), "recipient": recipient.get("externalId"),
        "message": {"id": provider_message_id, "from": sender.get("externalId"), "kind": message.get("kind") or "text", "text": message.get("content") or ""},
        "media": media, "provider": "meta",
        "providerDelivery": {"provider": "meta", "providerMessageId": provider_message_id, "connectionId": connection_id, "channelId": "instagram", "eventId": canonical.get("eventId"), "requestId": trace.get("requestId"), "correlationId": trace.get("correlationId")},
        "meta": {"connectionId": connection_id, "channelId": "instagram", "providerAccountId": transport.get("providerAccountRef"), "requestId": trace.get("requestId")},
        "correlationId": trace.get("correlationId"),
    }


def _trace(
    stage: str,
    *,
    request_id: str,
    started_at: float,
    instance_name: str | None = None,
    phone_number_id: str | None = None,
    waba_id: str | None = None,
    message: dict[str, Any] | None = None,
    result: str | None = None,
    **extra: Any,
) -> None:
    """Temporary, secret-safe Cloud API reception trace.

    Full payload capture is deliberately handled separately and only when
    WEBHOOK_DEBUG is enabled.  This event is safe to keep at info level: it
    records routing identifiers and never request headers or credentials.
    """
    item = message if isinstance(message, dict) else {}
    logger.info(
        "[META_TRACE] reception stage",
        stage=stage,
        result=result,
        request_id=request_id,
        instance_name=instance_name,
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        message_id=item.get("id"),
        sender=item.get("from"),
        message_type=item.get("type"),
        source_timestamp=item.get("timestamp"),
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        **extra,
    )


def _constant_time_equals(actual: str | None, expected: str) -> bool:
    return bool(actual and expected and hmac.compare_digest(actual, expected))


def _signature_is_valid(body: bytes, signature: str | None, app_secret: str) -> bool:
    if not signature or not signature.startswith("sha256=") or not app_secret:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def _webhook_signature_validations(body: bytes, signature: str | None, settings: Any) -> tuple[bool, bool]:
    """Return signature validity for the two independent Meta app credentials.

    The endpoint is shared, so the signed raw body is checked against both
    configured secrets before inspecting its provider object.  The caller then
    requires the matching result for that object; a valid signature from the
    other app is never sufficient.
    """
    return (
        _signature_is_valid(body, signature, str(getattr(settings, "instagram_app_secret", "") or "")),
        _signature_is_valid(body, signature, str(getattr(settings, "meta_app_secret", "") or "")),
    )


@router.get("/meta", response_class=PlainTextResponse, include_in_schema=False)
async def verify_meta_webhook(request: Request) -> PlainTextResponse:
    """Return exactly hub.challenge when Meta validates the subscription."""
    settings = get_settings()
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    logger.info(
        "[META_INBOUND][VERIFY] webhook verification requested",
        mode=mode,
        has_token=bool(token),
        has_challenge=bool(challenge),
    )
    if mode != "subscribe" or not challenge or not _constant_time_equals(token, settings.meta_webhook_verify_token):
        logger.warning(
            "[META_INBOUND][VERIFY][FAILED] webhook verification rejected",
            mode=mode,
            has_token=bool(token),
            has_challenge=bool(challenge),
            reason="invalid_mode_missing_challenge_or_verify_token",
        )
        audit_event("meta_webhook_verification_failed", mode=mode, hasToken=bool(token), hasChallenge=bool(challenge))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Meta webhook verification failed")
    logger.info("[META_INBOUND][VERIFY][OK] webhook verification completed")
    audit_event("meta_webhook_verified")
    return PlainTextResponse(challenge, status_code=status.HTTP_200_OK)


def _phone_number_id(value: dict[str, Any]) -> str:
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    return str(metadata.get("phone_number_id") or "").strip()


def _message_data(message: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, Any]:
    sender = str(message.get("from") or "").strip()
    message_type = str(message.get("type") or "unknown").strip()
    content = message.get(message_type) if isinstance(message.get(message_type), dict) else {}
    push_name = next((str((item.get("profile") or {}).get("name") or "") for item in contacts if str(item.get("wa_id") or "") == sender), "")
    key = {"id": str(message.get("id") or ""), "remoteJid": f"{sender}@s.whatsapp.net", "fromMe": False}
    # The normalizer accepts Evolution-shaped messages.  Preserve the original
    # Cloud object under the type key so media/interactive payloads remain visible.
    if message_type == "text":
        message_object: dict[str, Any] = {"conversation": str(content.get("body") or "")}
        normalized_type = "conversation"
    elif message_type == "interactive":
        reply = content.get("button_reply") or content.get("list_reply") or {}
        message_object = {"interactiveResponseMessage": {"body": {"text": str(reply.get("title") or reply.get("id") or "")}, "nativeFlowResponseMessage": {"paramsJson": json.dumps(reply)}}}
        normalized_type = "interactiveResponseMessage"
    else:
        key_name = {"image": "imageMessage", "audio": "audioMessage", "video": "videoMessage", "document": "documentMessage", "sticker": "stickerMessage", "location": "locationMessage", "contacts": "contactsArrayMessage", "reaction": "reactionMessage"}.get(message_type, message_type)
        message_object = {key_name: content}
        normalized_type = key_name
    return {"key": key, "pushName": push_name or None, "messageTimestamp": message.get("timestamp"), "messageType": normalized_type, "message": message_object}


async def _process_cloud_event(
    *,
    instance: str,
    event: dict[str, Any],
    trace_started_at: float,
    trace_request_id: str,
    trace_phone_number_id: str,
    trace_waba_id: str,
    trace_message: dict[str, Any],
) -> str:
    # Keep the Meta HTTP request correlation stable through parser,
    # normalizer, persistence and workspace dispatch.
    request_id = trace_request_id
    _trace(
        "pipeline_start",
        request_id=request_id,
        started_at=trace_started_at,
        instance_name=instance,
        phone_number_id=trace_phone_number_id,
        waba_id=trace_waba_id,
        message=trace_message,
    )
    result = process_incoming_webhook(event, request_id)
    normalized = result.get("normalized") or {}
    normalized_message = normalized.get("message") if isinstance(normalized.get("message"), dict) else {}
    _trace(
        "normalized",
        request_id=request_id,
        started_at=trace_started_at,
        instance_name=instance,
        phone_number_id=trace_phone_number_id,
        waba_id=trace_waba_id,
        message=trace_message,
        result=str(result.get("status") or "unknown"),
        normalized_message_id=normalized_message.get("id"),
        normalized_from=normalized_message.get("from"),
        normalized_type=normalized.get("subtype"),
        conversation_id=(normalized.get("meta") or {}).get("conversationId"),
        persistence=(result.get("trace") or {}).get("persist", {}).get("status"),
    )
    if result.get("status") != "ok":
        outcome = str(result.get("status") or "ignored")
        logger.warning(
            "[META_INBOUND][PIPELINE][STOPPED] message was not eligible for forwarding",
            request_id=request_id,
            instance=instance,
            outcome=outcome,
            trace=result.get("trace"),
        )
        _trace(
            "pipeline_stopped",
            request_id=request_id,
            started_at=trace_started_at,
            instance_name=instance,
            phone_number_id=trace_phone_number_id,
            waba_id=trace_waba_id,
            message=trace_message,
            result=outcome,
        )
        return outcome

    message = normalized.get("message") if isinstance(normalized.get("message"), dict) else {}
    sender = str(message.get("from") or "").strip() or None
    conversation_id = str((normalized.get("meta") or {}).get("conversationId") or "").strip() or None
    contact_name = message.get("pushName") or None
    # The Gateway has no contact/conversation repository.  These two logs make
    # that intentional contract visible during the incoming-message audit.
    logger.info(
        "[META_INBOUND][CONTACT] contact context resolved",
        request_id=request_id,
        instance=instance,
        sender=sender,
        contact_name=contact_name,
        operation="no_contact_entity_store",
    )
    logger.info(
        "[META_INBOUND][CONVERSATION] conversation id derived",
        request_id=request_id,
        instance=instance,
        conversation_id=conversation_id,
        operation="no_conversation_entity_store",
    )
    payload = _to_bot_payload(normalized)
    if not payload:
        logger.warning(
            "[META_INBOUND][FRONTEND][SKIPPED] normalized event has no dispatchable business payload",
            request_id=request_id,
            instance=instance,
            layer=normalized.get("layer"),
            event_type=normalized.get("type"),
        )
        _trace(
            "workspace_dispatch_skipped",
            request_id=request_id,
            started_at=trace_started_at,
            instance_name=instance,
            phone_number_id=trace_phone_number_id,
            waba_id=trace_waba_id,
            message=trace_message,
            result="not_forwarded",
        )
        return "not_forwarded"
    if len(_forward_tasks) >= get_settings().bot_webhook_max_queue:
        logger.error(
            "[META_INBOUND][FRONTEND][FAILED] outbound event queue is full",
            request_id=request_id,
            instance=instance,
            queue_size=len(_forward_tasks),
        )
        return "queued_dropped"
    _track_background_task(
        asyncio.create_task(_forward_to_instance_webhooks(payload, request_id)),
        request_id=request_id,
        instance=instance,
        message_id=str(message.get("id") or "") or None,
        conversation_id=conversation_id,
    )
    logger.info(
        "[META_INBOUND][FRONTEND] event queued for configured Gateway webhook targets",
        request_id=request_id,
        instance=instance,
        message_id=message.get("id") or None,
        conversation_id=conversation_id,
        queue_size=len(_forward_tasks),
        frontend_delivery="not_realtime; frontend reads /webhooks/events",
    )
    persist = result.get("trace", {}).get("persist", {})
    if persist.get("status") == "ok":
        logger.info(
            "[META_INBOUND][COMPLETE][OK] message processing completed successfully",
            request_id=request_id,
            instance=instance,
            message_id=message.get("id") or None,
            conversation_id=conversation_id,
        )
    else:
        logger.error(
            "[META_INBOUND][COMPLETE][FAILED] forwarding was queued but persistence failed",
            request_id=request_id,
            instance=instance,
            message_id=message.get("id") or None,
            conversation_id=conversation_id,
            error=persist.get("error") or "missing_persistence_confirmation",
        )
    _trace(
        "timeline_persisted_and_workspace_queued",
        request_id=request_id,
        started_at=trace_started_at,
        instance_name=instance,
        phone_number_id=trace_phone_number_id,
        waba_id=trace_waba_id,
        message=trace_message,
        result="queued",
        conversation_id=conversation_id,
        persistence=persist.get("status"),
    )
    return "queued"


@router.post("/meta", status_code=status.HTTP_200_OK, include_in_schema=False)
async def receive_meta_webhook(request: Request) -> dict[str, Any]:
    """Accept Meta-signed WhatsApp messages, status changes and errors."""
    settings = get_settings()
    body = await request.body()
    request_id = str(uuid.uuid4())[:12]
    started_at = time.perf_counter()
    source_ip = request.client.host if request.client else "unknown"
    signature = request.headers.get("X-Hub-Signature-256")
    logger.info(
        "[META_INBOUND][RECEIVED] webhook received",
        request_id=request_id,
        source_ip=source_ip,
        content_length=len(body),
        signature_required=bool(settings.meta_webhook_require_signature),
        has_signature=bool(signature),
    )
    _trace("gateway_received", request_id=request_id, started_at=started_at)
    instagram_signature_valid, whatsapp_signature_valid = _webhook_signature_validations(body, signature, settings)
    if settings.meta_webhook_require_signature and not (instagram_signature_valid or whatsapp_signature_valid):
        logger.warning(
            "[META_INBOUND][SIGNATURE][FAILED] invalid Meta webhook signature",
            request_id=request_id,
            has_signature=bool(signature),
            has_instagram_app_secret=bool(getattr(settings, "instagram_app_secret", "")),
            has_meta_app_secret=bool(settings.meta_app_secret),
            reason="missing_or_invalid_x_hub_signature_256",
        )
        audit_event("meta_webhook_signature_invalid")
        _trace("signature", request_id=request_id, started_at=started_at, result="invalid")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Meta webhook signature")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[META_INBOUND][PAYLOAD][FAILED] invalid JSON payload",
            request_id=request_id,
            error=str(exc),
        )
        _trace("payload_parse", request_id=request_id, started_at=started_at, result="invalid_json")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Meta webhook JSON") from exc
    if not isinstance(payload, dict):
        logger.warning(
            "[META_INBOUND][PAYLOAD][FAILED] unsupported webhook object",
            request_id=request_id,
            object=payload.get("object") if isinstance(payload, dict) else None,
            payload_type=type(payload).__name__,
        )
        _trace("payload_parse", request_id=request_id, started_at=started_at, result="unsupported_object")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported Meta webhook object")

    webhook_object = payload.get("object")
    signature_valid = (
        instagram_signature_valid if webhook_object == "instagram"
        else whatsapp_signature_valid if webhook_object == "whatsapp_business_account"
        else instagram_signature_valid or whatsapp_signature_valid
    )
    if settings.meta_webhook_require_signature and not signature_valid:
        logger.warning(
            "[META_INBOUND][SIGNATURE][FAILED] signature belongs to a different Meta app",
            request_id=request_id,
            webhook_object=webhook_object,
            has_signature=bool(signature),
            reason="signature_not_valid_for_webhook_object",
        )
        audit_event("meta_webhook_signature_invalid", webhookObject=webhook_object)
        _trace("signature", request_id=request_id, started_at=started_at, result="invalid")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Meta webhook signature")
    logger.info(
        "[META_INBOUND][SIGNATURE][OK] Meta webhook signature accepted",
        request_id=request_id,
        webhook_object=webhook_object,
        validation="required" if settings.meta_webhook_require_signature else "disabled_by_configuration",
    )
    _trace("signature", request_id=request_id, started_at=started_at, result="valid")

    if webhook_object == "instagram":
        try:
            canonical = process_instagram_webhook(payload, request_id=request_id)
        except InstagramWebhookError as exc:
            logger.warning(
                "instagram_webhook_rejected",
                request_id=request_id,
                provider="meta",
                channel_type="instagram",
                status_code=exc.status_code,
                reason=str(exc),
                provider_account_id=exc.provider_account_id,
                connection_lookup_result=exc.connection_lookup_result,
            )
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        try:
            persisted = get_core_inbound_dispatcher().persist_many(canonical)
        except CoreInboundPersistenceError as exc:
            logger.error(
                "instagram_webhook_handoff_persistence_failed",
                request_id=request_id,
                provider="meta",
                channel_type="instagram",
                error=str(exc),
            )
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Instagram event handoff persistence failed") from exc
        for event in canonical:
            timeline_event = _instagram_timeline_event(event)
            if timeline_event is not None:
                save_event(timeline_event)
        messaging_count = sum(
            len(entry.get("messaging") or [])
            for entry in payload.get("entry") or []
            if isinstance(entry, dict) and isinstance(entry.get("messaging"), list)
        )
        for event in canonical:
            logger.info(
                "instagram_webhook_canonical_event_created",
                request_id=request_id,
                event_id=event["eventId"],
                provider_account_id=event["transport"]["providerAccountRef"],
                connection_id=event["transport"]["connectionRef"],
                provider="meta",
                channel_type="instagram",
                event_type=event["eventType"],
            )
        # G3 intentionally stops at the G4 handoff boundary. No Core dispatch,
        # inbound entity persistence or background task is created here.
        result = {"status": "ok", "object": "instagram", "canonicalEvents": len(canonical), "acknowledged": messaging_count}
        audit_event("instagram_webhook_received", requestId=request_id, canonicalEvents=len(canonical), persistedDeliveries=len(persisted), acknowledged=messaging_count)
        return result

    if webhook_object != "whatsapp_business_account":
        logger.warning(
            "[META_INBOUND][PAYLOAD][FAILED] unsupported webhook object",
            request_id=request_id,
            object=payload.get("object"),
        )
        _trace("payload_parse", request_id=request_id, started_at=started_at, result="unsupported_object")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported Meta webhook object")

    logger.info(
        "[META_INBOUND][PAYLOAD][OK] payload validated",
        request_id=request_id,
        entries=len(payload.get("entry") or []),
    )
    _trace("payload_parse", request_id=request_id, started_at=started_at, result="ok")
    if bool(getattr(settings, "webhook_debug", False)):
        # Meta webhook payloads do not include the app secret or bearer token.
        # This is intentionally opt-in because it can contain customer content.
        logger.info("[META_TRACE][RAW_PAYLOAD]", request_id=request_id, payload=payload)

    received = {"messages": 0, "statuses": 0, "changes": 0, "errors": 0, "unmapped": 0}
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        waba_id = str(entry.get("id") or "").strip()
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            received["changes"] += 1
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            phone_id = _phone_number_id(value)
            _trace(
                "change_parsed",
                request_id=request_id,
                started_at=started_at,
                phone_number_id=phone_id or None,
                waba_id=waba_id or None,
                result="ok",
                field=change.get("field"),
            )
            logger.info(
                "[META_INBOUND][CHANGE] Meta change detected",
                request_id=request_id,
                field=change.get("field"),
                phone_number_id=phone_id or None,
                messages=len(value.get("messages") or []),
                statuses=len(value.get("statuses") or []),
                errors=len(value.get("errors") or []),
            )
            instance = get_credential_manager().find_instance_by_phone_number_id(phone_id)
            if not instance:
                received["unmapped"] += 1
                logger.warning(
                    "[META_INBOUND][INSTANCE][FAILED] no Gateway instance matches Meta phone number",
                    request_id=request_id,
                    phone_number_id=phone_id or None,
                    field=change.get("field"),
                    reason="phone_number_id_not_found_in_official_credentials",
                )
                audit_event("meta_webhook_instance_unmapped", phoneNumberId=phone_id or None, field=change.get("field"))
                _trace(
                    "instance_resolution",
                    request_id=request_id,
                    started_at=started_at,
                    phone_number_id=phone_id or None,
                    waba_id=waba_id or None,
                    result="unmapped",
                    field=change.get("field"),
                )
                continue
            logger.info(
                "[META_INBOUND][INSTANCE][OK] Gateway instance resolved",
                request_id=request_id,
                instance=instance,
                phone_number_id=phone_id or None,
            )
            _trace(
                "instance_resolution",
                request_id=request_id,
                started_at=started_at,
                instance_name=instance,
                phone_number_id=phone_id or None,
                waba_id=waba_id or None,
                result="resolved",
                field=change.get("field"),
            )
            save_pipeline_event(
                stage="meta_webhook_change",
                status="received",
                instance=instance,
                event=str(change.get("field") or "unknown"),
                details={
                    "hasMessages": bool(value.get("messages")),
                    "hasStatuses": bool(value.get("statuses")),
                    "hasErrors": bool(value.get("errors")),
                },
            )
            contacts = [item for item in value.get("contacts") or [] if isinstance(item, dict)]
            for message in value.get("messages") or []:
                if not isinstance(message, dict):
                    logger.warning("[META_INBOUND][MESSAGE][SKIPPED] messages item is not an object", request_id=request_id, instance=instance)
                    continue
                received["messages"] += 1
                logger.info(
                    "[META_INBOUND][MESSAGE] incoming message detected",
                    request_id=request_id,
                    instance=instance,
                    message_id=message.get("id") or None,
                    sender=message.get("from") or None,
                    message_type=message.get("type") or None,
                )
                try:
                    _trace(
                        "message_parsed",
                        request_id=request_id,
                        started_at=started_at,
                        instance_name=instance,
                        phone_number_id=phone_id or None,
                        waba_id=waba_id or None,
                        message=message,
                        result="ok",
                    )
                    outcome = await _process_cloud_event(
                        instance=instance,
                        event={"event": "MESSAGES_UPSERT", "instance": instance, "data": _message_data(message, contacts), "metaCloud": payload},
                        trace_started_at=started_at,
                        trace_request_id=request_id,
                        trace_phone_number_id=phone_id,
                        trace_waba_id=waba_id,
                        trace_message=message,
                    )
                    logger.info(
                        "[META_INBOUND][MESSAGE][RESULT] message pipeline finished",
                        request_id=request_id,
                        instance=instance,
                        message_id=message.get("id") or None,
                        outcome=outcome,
                    )
                except Exception as exc:
                    logger.exception(
                        "[META_INBOUND][MESSAGE][FAILED] unhandled pipeline exception",
                        request_id=request_id,
                        instance=instance,
                        message_id=message.get("id") or None,
                        error=str(exc),
                    )
                    raise
            for item in value.get("statuses") or []:
                if not isinstance(item, dict):
                    continue
                received["statuses"] += 1
                await _process_cloud_event(
                    instance=instance,
                    event={"event": "MESSAGES_UPDATE", "instance": instance, "provider": "meta", "data": {"key": {"id": str(item.get("id") or ""), "remoteJid": str(item.get("recipient_id") or ""), "fromMe": True}, "status": item.get("status"), "timestamp": item.get("timestamp")}, "metaCloud": payload},
                    trace_started_at=started_at,
                    trace_request_id=request_id,
                    trace_phone_number_id=phone_id,
                    trace_waba_id=waba_id,
                    trace_message=item,
                )
            for error in value.get("errors") or []:
                received["errors"] += 1
                logger.error("meta_webhook_provider_error", instance=instance, error_code=error.get("code") if isinstance(error, dict) else None)
                save_pipeline_event(stage="meta_webhook_error", status="received", instance=instance, details={"error": error})
    logger.info("[META_INBOUND][RESPONSE][OK] responding HTTP 200 to Meta", request_id=request_id, **received)
    _trace("http_response", request_id=request_id, started_at=started_at, result="200", **received)
    audit_event("meta_webhook_received", **received)
    return {"status": "ok", **received}
