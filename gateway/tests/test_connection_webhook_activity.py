from __future__ import annotations

from app.services.clients import ClientService
from app.services.connection_registry import ConnectionRegistry
from app.services.connection_webhook_activity import ConnectionWebhookActivityService


def _connection(registry: ConnectionRegistry, *, connection_id: str, legacy_name: str, provider: str, channel: str) -> None:
    client = ClientService(registry).create_client("Activity owner")
    saved = registry.save_connection_record_for_client(legacy_name, {
        "id": connection_id,
        "legacy_name": legacy_name,
        "client_id": client.id,
        "name": "Activity connection",
        "provider_id": provider,
        "channel_id": channel,
        "status_state": "connected",
        "status_health": "healthy",
    })
    assert saved is not None


def test_instagram_activity_projects_core_and_external_transport_without_sensitive_content(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connections.json")
    _connection(registry, connection_id="instagram-connection", legacy_name="instagram-runtime", provider="meta", channel="instagram")

    service = ConnectionWebhookActivityService(
        registry=registry,
        events_reader=lambda **_kwargs: [],
        external_deliveries_reader=lambda _instance, **_kwargs: [{
            "id": "external-1", "timestamp": 1_700_000_030_000, "success": True,
            "statusCode": 204, "durationMs": 14, "attemptCount": 1,
            "eventType": "message.created", "eventId": "event-1",
            "destinationUrl": "https://private.example/hook?token=do-not-expose",
            "headers": {"Authorization": "Bearer do-not-expose"},
        }],
        core_deliveries_reader=lambda _connection_id, **_kwargs: [{
            "id": "core-1", "event_id": "event-1", "event_type": "message.created",
            "provider_message_id": "mid-1", "created_at": 1_700_000_000_000,
            "last_attempt_at": 1_700_000_010_000, "status": "delivered", "attempt_count": 1,
            "request_id": "request-1", "correlation_id": "correlation-1",
            "core_channel_id": "channel-private", "text": "private incoming message",
            "last_error": "Bearer do-not-expose https://private.example/diagnostic",
            "attachments": [{"url": "https://private.example/media"}],
        }],
    )

    items = service.list("instagram-connection")

    assert [(item["source"], item["destination"], item["status"]) for item in items] == [
        ("gateway", "external_webhook", "delivered_to_external_webhook"),
        ("gateway", "core", "delivered_to_core"),
        ("provider", "gateway", "normalized"),
    ]
    core_delivery = next(item for item in items if item["destination"] == "core")
    assert core_delivery["http_status"] is None
    assert core_delivery["request_id"] == "request-1"
    assert core_delivery["error"] == "transport_error_recorded_details_redacted"
    assert set(core_delivery) == {
        "id", "timestamp", "direction", "source", "destination", "event_type", "status", "http_status",
        "duration_ms", "event_id", "provider_message_id", "request_id", "correlation_id", "error", "attempt_count",
    }
    rendered = str(items)
    assert "private incoming message" not in rendered
    assert "private.example" not in rendered
    assert "do-not-expose" not in rendered
    assert "channel-private" not in rendered


def test_generic_provider_ingress_uses_normalized_evidence_only(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connections.json")
    _connection(registry, connection_id="whatsapp-connection", legacy_name="whatsapp-runtime", provider="evolution", channel="whatsapp")

    service = ConnectionWebhookActivityService(
        registry=registry,
        events_reader=lambda **_kwargs: [{
            "id": "normalization-1", "direction": "inbound", "timestamp": 1_700_000_100_000,
            "eventType": "messages.upsert", "message": {"id": "mid-2", "text": "private message"},
            "providerDelivery": {"eventId": "event-2", "requestId": "request-2"},
            "meta": {"requestId": "request-2", "authorization": "Bearer private-token"},
            "correlationId": "correlation-2", "raw": {"payload": "private payload"},
        }],
        external_deliveries_reader=lambda _instance, **_kwargs: [],
        core_deliveries_reader=lambda *_args, **_kwargs: [],
    )

    items = service.list("whatsapp-connection")

    assert len(items) == 1
    assert items[0]["source"] == "provider"
    assert items[0]["destination"] == "gateway"
    assert items[0]["status"] == "normalized"
    assert items[0]["event_id"] == "event-2"
    assert "private message" not in str(items)
    assert "private-token" not in str(items)
    assert "private payload" not in str(items)
