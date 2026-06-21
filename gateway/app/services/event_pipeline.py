from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.normalization import normalize_webhook, save_event, save_pipeline_event, save_raw_event
from app.services.reliability import conversation_id, inbound_dedupe, is_flood, looks_like_outbound_echo, message_fingerprint

logger = get_logger(__name__)
settings = get_settings()

_pipeline_counters: dict[str, int] = defaultdict(int)
_unknown_type_counters: dict[str, int] = defaultdict(int)


def snapshot_pipeline_metrics() -> dict[str, Any]:
    return {
        "counters": dict(_pipeline_counters),
        "unknownTypes": dict(_unknown_type_counters),
    }


def _inc(name: str, value: int = 1) -> None:
    _pipeline_counters[name] += value


def _event_age_seconds(normalized: dict[str, Any], now_ms: int) -> int | None:
    source_timestamp = normalized.get("sourceTimestamp")
    if source_timestamp is None:
        return None
    try:
        source_ms = int(source_timestamp)
    except (TypeError, ValueError):
        return None
    if source_ms <= 0:
        return None
    return max(0, now_ms - source_ms) // 1000


def _category_of(normalized: dict[str, Any]) -> str:
    layer = str(normalized.get("layer") or "")
    if layer == "technical":
        return "transport"
    ntype = str(normalized.get("type") or "")
    if ntype == "message":
        return "business_message"
    if ntype == "event":
        return "business_event"
    return "operational"


def _classify(payload: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    source_event = str(payload.get("event") or "UNKNOWN")
    message_type = str((payload.get("data") or {}).get("messageType") or "")
    return {
        "sourceEvent": source_event,
        "messageType": message_type or None,
        "layer": normalized.get("layer"),
        "category": _category_of(normalized),
        "subtype": normalized.get("subtype"),
        "originalType": normalized.get("originalType"),
        "fallbackUsed": bool((normalized.get("metadata") or {}).get("unknownTypeDetected")),
    }


def _enrich_contract(
    normalized: dict[str, Any],
    *,
    request_id: str,
    conv_id: str,
    trace: dict[str, Any],
    classification: dict[str, Any],
) -> dict[str, Any]:
    normalized["meta"] = {"requestId": request_id, "conversationId": conv_id}
    normalized["eventType"] = normalized.get("type")
    normalized["category"] = classification.get("category")
    normalized["transport"] = {
        "sourceEvent": normalized.get("sourceEvent"),
        "event": normalized.get("event"),
        "sourceTimestamp": normalized.get("sourceTimestamp"),
    }
    normalized["operational"] = {
        "pipeline": trace,
    }
    return normalized


def process_incoming_webhook(payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    started_at = int(time.time() * 1000)
    instance = str(payload.get("instance", "unknown"))
    trace: dict[str, Any] = {"ingest": {"status": "ok", "at": started_at}}
    _inc("ingest_total")
    logger.info("[INBOUND][RECEIVED] webhook payload received", request_id=request_id, instance=instance, source_event=payload.get("event"))

    try:
        normalized = normalize_webhook(payload)
    except Exception as exc:
        trace["normalize"] = {"status": "error", "error": str(exc)}
        _inc("normalize_error_total")
        logger.error("pipeline_normalize_fail", request_id=request_id, instance=instance, error=str(exc))
        save_pipeline_event(
            stage="normalize",
            status="error",
            instance=instance,
            request_id=request_id,
            details={"error": str(exc)[:220]},
        )
        return {"status": "normalize_error", "normalized": {}, "trace": trace, "classification": {}}
    classification = _classify(payload, normalized)
    trace["classify"] = {"status": "ok", **classification}

    if classification.get("fallbackUsed"):
        _inc("normalization_fallback_total")
    if bool((normalized.get("metadata") or {}).get("unknownTypeDetected")):
        _unknown_type_counters[str(normalized.get("originalType") or "unknown")] += 1

    if normalized.get("layer") == "technical":
        logger.info("[MESSAGE][FILTERED] technical event ignored", request_id=request_id, instance=instance, reason=normalized.get("reason"))
        trace["route"] = {"status": "ignored", "reason": normalized.get("reason") or "technical_event"}
        _inc("transport_ignored_total")
        save_pipeline_event(
            stage="route",
            status="ignored_transport",
            instance=instance,
            request_id=request_id,
            event=str(normalized.get("event") or payload.get("event") or "UNKNOWN"),
            details={"reason": normalized.get("reason")},
        )
        return {"status": "ignored_technical", "normalized": normalized, "trace": trace, "classification": classification}

    message = normalized.get("message") or {}
    msg_id = str(message.get("id") or "")
    conv_id = conversation_id(instance, message.get("from"))
    normalized = _enrich_contract(normalized, request_id=request_id, conv_id=conv_id, trace=trace, classification=classification)
    trace["enrich"] = {"status": "ok", "conversationId": conv_id}

    event = str(normalized.get("event") or payload.get("event") or "UNKNOWN")
    save_pipeline_event(stage="ingest", status="ok", instance=instance, request_id=request_id, event=event)

    max_age_seconds = max(0, int(settings.max_event_age_seconds or 0))
    event_age_seconds = _event_age_seconds(normalized, started_at)
    if event_age_seconds is not None:
        trace["age"] = {
            "status": "ok",
            "ageSeconds": event_age_seconds,
            "maxAgeSeconds": max_age_seconds,
            "sourceTimestamp": normalized.get("sourceTimestamp"),
        }
    elif max_age_seconds > 0:
        trace["age"] = {"status": "missing_source_timestamp", "maxAgeSeconds": max_age_seconds}
        _inc("stale_guard_missing_timestamp_total")
    if max_age_seconds > 0 and event_age_seconds is not None and event_age_seconds > max_age_seconds:
        logger.warning(
            "event dropped: stale message",
            request_id=request_id,
            instance=instance,
            source_event=event,
            message_id=msg_id or None,
            age_seconds=event_age_seconds,
            max_age_seconds=max_age_seconds,
        )
        save_pipeline_event(
            stage="stale_guard",
            status="dropped_stale",
            instance=instance,
            message_id=msg_id or None,
            conversation_id=conv_id,
            request_id=request_id,
            event=event,
            details={
                "ageSeconds": event_age_seconds,
                "maxAgeSeconds": max_age_seconds,
                "sourceTimestamp": normalized.get("sourceTimestamp"),
            },
        )
        trace["route"] = {"status": "dropped", "reason": "stale_event", "ageSeconds": event_age_seconds}
        _inc("stale_dropped_total")
        return {"status": "stale_dropped", "normalized": normalized, "trace": trace, "classification": classification}

    try:
        save_raw_event({"requestId": request_id, "payload": payload, "normalized": normalized, "timestamp": int(time.time() * 1000)})
    except Exception as exc:
        trace["ingest"]["rawPersistError"] = str(exc)
        logger.warning("pipeline_raw_event_persist_fail", request_id=request_id, instance=instance, error=str(exc))

    if event in {"MESSAGES_UPSERT", "SEND_MESSAGE"}:
        logger.info(
            "pipeline_ingest",
            request_id=request_id,
            instance=instance,
            event_type=event,
            message_id=msg_id or None,
            subtype=normalized.get("subtype"),
            direction=normalized.get("direction"),
        )
        if msg_id and inbound_dedupe.exists(msg_id):
            logger.info("[MESSAGE][FILTERED] duplicate by id", request_id=request_id, instance=instance, message_id=msg_id)
            save_pipeline_event(stage="dedupe", status="skipped_duplicate_id", instance=instance, message_id=msg_id, conversation_id=conv_id, request_id=request_id)
            trace["route"] = {"status": "skipped", "reason": "duplicate_id"}
            _inc("dedupe_message_id_total")
            return {"status": "duplicate", "normalized": normalized, "trace": trace, "classification": classification}
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
            logger.info("[MESSAGE][FILTERED] duplicate by fingerprint", request_id=request_id, instance=instance, message_id=msg_id or None)
            save_pipeline_event(stage="dedupe", status="skipped_duplicate_fingerprint", instance=instance, message_id=msg_id, conversation_id=conv_id, request_id=request_id)
            trace["route"] = {"status": "skipped", "reason": "duplicate_fingerprint"}
            _inc("dedupe_fingerprint_total")
            return {"status": "duplicate_fp", "normalized": normalized, "trace": trace, "classification": classification}
        inbound_dedupe.put(fp)

        is_from_me = bool(message.get("fromMe"))
        if is_from_me:
            normalized["status"] = "sent"
            normalized["forwarding"] = {"status": "not_forwarded_from_me"}
            normalized["fromBot"] = False

        payload_text = str(message.get("text") or (normalized.get("media") or {}).get("caption") or "")
        if (not is_from_me) and looks_like_outbound_echo(instance, message.get("from"), str(message.get("kind") or "unknown"), payload_text):
            logger.info("[MESSAGE][FILTERED] outbound echo", request_id=request_id, instance=instance, message_id=msg_id or None)
            save_pipeline_event(stage="anti_loop", status="skipped_outbound_echo", instance=instance, message_id=msg_id, conversation_id=conv_id, request_id=request_id)
            trace["route"] = {"status": "skipped", "reason": "outbound_echo"}
            _inc("anti_loop_echo_total")
            return {"status": "echo_filtered", "normalized": normalized, "trace": trace, "classification": classification}

        flooded, count = is_flood(conv_id)
        if flooded:
            logger.info("[MESSAGE][FILTERED] flood throttled", request_id=request_id, instance=instance, conversation_id=conv_id, count=count)
            save_pipeline_event(
                stage="flood_guard",
                status="throttled",
                instance=instance,
                message_id=msg_id,
                conversation_id=conv_id,
                request_id=request_id,
                details={"messagesInWindow": count},
            )
            trace["route"] = {"status": "skipped", "reason": "throttled", "messagesInWindow": count}
            _inc("flood_throttled_total")
            return {"status": "throttled", "normalized": normalized, "trace": trace, "classification": classification}

    try:
        save_event(normalized)
        logger.info("[MESSAGE][UPSERT] persisted", request_id=request_id, instance=instance, message_id=msg_id or None, direction=normalized.get("direction"), subtype=normalized.get("subtype"))
        trace["persist"] = {"status": "ok"}
        _inc("persist_ok_total")
    except Exception as exc:
        trace["persist"] = {"status": "error", "error": str(exc)}
        _inc("persist_error_total")
        logger.error("pipeline_persist_fail", request_id=request_id, instance=instance, error=str(exc))

    save_pipeline_event(
        stage="normalized",
        status="ok",
        instance=instance,
        message_id=msg_id or None,
        conversation_id=conv_id,
        request_id=request_id,
    )

    trace["route"] = {
        "status": "ok",
        "toTimeline": True,
        "toBotCandidate": bool(normalized.get("layer") == "business"),
    }
    _inc("business_processed_total")
    return {"status": "ok", "normalized": normalized, "trace": trace, "classification": classification}
