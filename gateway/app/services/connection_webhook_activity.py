"""Safe, provider-neutral projection of connection transport activity."""
from __future__ import annotations

from typing import Any, Callable

from app.services.connection_registry import ConnectionRegistry, get_connection_registry
from app.services.core_inbound_dispatcher import get_core_inbound_dispatcher
from app.services.normalization import list_events
from app.services.instance_webhooks import list_recent_dispatches


def _text(value: Any, *, limit: int = 300) -> str | None:
    value = str(value or "").strip()
    return value[:limit] if value else None


def _timestamp(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_error(value: Any) -> str | None:
    # Upstream error strings are unstructured and can echo authorization
    # headers, URLs, or payload fragments. Keep the browser projection useful
    # without trusting such text to be safe; correlation IDs lead operators to
    # the protected server logs for the original diagnostic.
    return "transport_error_recorded_details_redacted" if _text(value) else None


class ConnectionWebhookActivityService:
    """Combine existing safe stores without merging their persistence models.

    The projection deliberately contains transport evidence only. It does not
    leak provider payloads, external destinations, authentication material, or
    media URLs into the browser.
    """

    def __init__(
        self,
        *,
        registry: ConnectionRegistry | None = None,
        events_reader: Callable[..., list[dict[str, Any]]] = list_events,
        external_deliveries_reader: Callable[..., list[dict[str, Any]]] = list_recent_dispatches,
        core_deliveries_reader: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        self._registry = registry or get_connection_registry()
        self._events_reader = events_reader
        self._external_deliveries_reader = external_deliveries_reader
        self._core_deliveries_reader = core_deliveries_reader or get_core_inbound_dispatcher().list_connection_deliveries

    def list(self, connection_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise KeyError(connection_id)
        safe_limit = max(1, min(int(limit), 200))
        provider = _text(record.get("provider_id")) or "gateway"
        channel = _text(record.get("channel_id")) or "whatsapp"
        runtime_name = _text(record.get("legacy_name")) or connection_id
        items: list[dict[str, Any]] = []

        # Every transport that persists normalized inbound evidence can appear
        # here. Instagram's durable canonical delivery is the more precise
        # source for its ingress and is added below instead.
        if not (provider == "meta" and channel == "instagram"):
            items.extend(self._provider_ingress(runtime_name, safe_limit))
        items.extend(self._external_deliveries(runtime_name, safe_limit))
        # The current durable Core dispatcher is populated by Instagram, but
        # projecting it for every Connection keeps the route adapter-neutral
        # as additional providers adopt the same transport contract.
        items.extend(self._core_activity(connection_id, safe_limit))

        items.sort(key=lambda item: (int(item.get("timestamp") or 0), str(item.get("id") or "")), reverse=True)
        return items[:safe_limit]

    def _provider_ingress(self, instance: str, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for event in self._events_reader(instance=instance, limit=limit):
            if not isinstance(event, dict) or _text(event.get("direction")) != "inbound":
                continue
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            delivery = event.get("providerDelivery") if isinstance(event.get("providerDelivery"), dict) else {}
            metadata = event.get("meta") if isinstance(event.get("meta"), dict) else {}
            event_id = _text(delivery.get("eventId")) or _text(event.get("eventId"))
            provider_message_id = _text(delivery.get("providerMessageId")) or _text(message.get("id"))
            timestamp = _timestamp(event.get("timestamp"))
            if not timestamp:
                continue
            items.append(self._activity(
                id=f"provider:{_text(event.get('id')) or timestamp}", timestamp=timestamp,
                direction="inbound", source="provider", destination="gateway", event_type=_text(event.get("eventType")) or _text(event.get("event")),
                status="normalized", event_id=event_id, provider_message_id=provider_message_id,
                request_id=_text(metadata.get("requestId")) or _text(delivery.get("requestId")),
                correlation_id=_text(event.get("correlationId")) or _text(delivery.get("correlationId")),
            ))
        return items

    def _core_activity(self, connection_id: str, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for delivery in self._core_deliveries_reader(connection_id, limit=limit):
            if not isinstance(delivery, dict):
                continue
            event_id = _text(delivery.get("event_id"))
            provider_message_id = _text(delivery.get("provider_message_id"))
            created_at = _timestamp(delivery.get("created_at"))
            if created_at:
                items.append(self._activity(
                    id=f"provider:{_text(delivery.get('id')) or event_id or created_at}", timestamp=created_at,
                    direction="inbound", source="provider", destination="gateway", event_type=_text(delivery.get("event_type")),
                    status="normalized", event_id=event_id, provider_message_id=provider_message_id,
                    request_id=_text(delivery.get("request_id")), correlation_id=_text(delivery.get("correlation_id")),
                ))
            status = _text(delivery.get("status")) or "failed"
            attempted_at = _timestamp(delivery.get("last_attempt_at")) or created_at
            if attempted_at:
                items.append(self._activity(
                    id=f"core:{_text(delivery.get('id')) or event_id or attempted_at}", timestamp=attempted_at,
                    direction="outbound", source="gateway", destination="core", event_type=_text(delivery.get("event_type")),
                    status=self._core_status(status), event_id=event_id, provider_message_id=provider_message_id,
                    request_id=_text(delivery.get("request_id")), correlation_id=_text(delivery.get("correlation_id")),
                    error=_safe_error(delivery.get("last_error")), attempt_count=max(0, int(delivery.get("attempt_count") or 0)),
                    http_status=409 if bool(delivery.get("duplicate_acknowledged")) else None,
                ))
        return items

    def _external_deliveries(self, instance: str, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for delivery in self._external_deliveries_reader(instance, limit=limit):
            if not isinstance(delivery, dict):
                continue
            status_code = _timestamp(delivery.get("statusCode")) or None
            success = bool(delivery.get("success"))
            raw_status = _text(delivery.get("status")) or "failed"
            items.append(self._activity(
                id=f"external:{_text(delivery.get('id')) or _timestamp(delivery.get('timestamp'))}", timestamp=_timestamp(delivery.get("timestamp")),
                direction="outbound", source="gateway", destination="external_webhook", event_type=_text(delivery.get("eventType")),
                status="delivered_to_external_webhook" if success else "retrying" if raw_status == "retrying" else "failed",
                event_id=_text(delivery.get("eventId")), request_id=_text(delivery.get("requestId")), correlation_id=_text(delivery.get("correlationId")),
                http_status=status_code, duration_ms=float(delivery.get("durationMs") or 0) or None,
                error=_safe_error(delivery.get("error")), attempt_count=max(0, int(delivery.get("attemptCount") or 0)),
            ))
        return items

    @staticmethod
    def _core_status(status: str) -> str:
        return {
            "pending": "queued", "retry": "retrying", "delivering": "dispatching",
            "delivered": "delivered_to_core", "dead_letter": "dead_letter", "failed": "failed",
        }.get(status, status)

    @staticmethod
    def _activity(**values: Any) -> dict[str, Any]:
        return {
            "id": values["id"], "timestamp": values["timestamp"], "direction": values["direction"],
            "source": values["source"], "destination": values["destination"], "event_type": values.get("event_type"),
            "status": values["status"], "http_status": values.get("http_status"), "duration_ms": values.get("duration_ms"),
            "event_id": values.get("event_id"), "provider_message_id": values.get("provider_message_id"),
            "request_id": values.get("request_id"), "correlation_id": values.get("correlation_id"),
            "error": values.get("error"), "attempt_count": values.get("attempt_count", 0),
        }


def get_connection_webhook_activity_service() -> ConnectionWebhookActivityService:
    return ConnectionWebhookActivityService()
