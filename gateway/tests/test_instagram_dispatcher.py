from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

import app.services.core_channel_credentials as core_credentials_module
import app.services.credential_manager as credential_manager_module
from app.services.clients import ClientService
from app.services.connection_registry import ConnectionRegistry
from app.services.connections import ConnectionService
from app.services.core_channel_credentials import CoreChannelCredentialStore
from app.services.core_inbound_dispatcher import CoreInboundDeliveryStore, CoreInboundDispatcher
from app.services.credential_manager import CredentialManager, ProviderAccountReference
from app.services.gateway_settings import GatewaySettingsService
from app.services.instagram_webhook import process_instagram_webhook


class _Runtime:
    async def list_instances(self):
        return []


def _settings(**changes):
    values = {
        "core_inbound_url": "https://core.example/api/v1/webhook/inbound",
        "core_inbound_delivery_batch_size": 25,
        "core_inbound_delivery_lease_seconds": 60,
        "core_inbound_delivery_max_attempts": 3,
        "core_inbound_delivery_backoff_base_seconds": 0,
        "bot_webhook_timeout": 2,
        "core_channel_credentials_encryption_key": "core-channel-test-key",
        "gateway_api_key": "gateway-key",
        "environment": "test",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _event(*, event_id: str = "event-1", account_id: str = "178400012345678", connection_id: str = "connection-1", message_id: str = "mid-1") -> dict:
    return {
        "eventId": event_id,
        "eventType": "message.created",
        "occurredAt": "2024-03-09T16:00:00Z",
        "transport": {"provider": "meta", "channelType": "instagram", "connectionRef": connection_id, "providerAccountRef": account_id},
        "message": {
            "providerMessageId": message_id,
            "direction": "inbound",
            "kind": "text",
            "content": "hola",
            "sender": {"externalId": "instagram_user_abc"},
            "recipient": {"externalId": account_id},
            "attachments": [],
        },
        "metadata": {},
        "trace": {"requestId": "request-1", "correlationId": "request-1"},
    }


def _bound_dispatcher(monkeypatch, tmp_path, handler):
    test_settings = _settings()
    monkeypatch.setattr(core_credentials_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(
        credential_manager_module,
        "get_settings",
        lambda: SimpleNamespace(
            official_credentials_path=str(tmp_path / "credentials.json"),
            official_credentials_encryption_key="",
            provider_credentials_encryption_key="provider-key",
            gateway_api_key="gateway-key",
            environment="test",
        ),
    )
    registry = ConnectionRegistry(tmp_path / "connections.json")
    settings = GatewaySettingsService(tmp_path / "gateway-settings.json")
    settings.update_channels({"instagram": True})
    client = ClientService(registry).create_client("Tenant A")
    core_credentials = CoreChannelCredentialStore(tmp_path / "core-channel-credentials.json")
    service = ConnectionService(_Runtime(), registry, settings, CredentialManager(), core_credentials)
    connection = service.create_connection(client_id=client.id, channel="instagram", provider="meta")
    account = ProviderAccountReference("meta", "instagram", "178400012345678")
    service._credentials.upsert_provider_credentials(
        account=account,
        access_token="meta-access-token-must-not-leave-gateway",
        access_token_ref="meta://instagram/account/token",
        source="test",
        scopes=("instagram_business_basic",),
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    service.bind_instagram_provider_account(connection_id=connection.id, account=account, metadata={}, required_scopes=())
    service.bind_instagram_core_channel(
        connection_id=connection.id,
        core_channel_id="core-channel-a",
        dispatch_credential="channel-api-key-a",
        core_binding_id="binding-a",
    )
    store = CoreInboundDeliveryStore(tmp_path / "deliveries.json")
    dispatcher = CoreInboundDispatcher(
        store=store,
        connections=service,
        credentials=core_credentials,
        settings_factory=lambda: test_settings,
        client_factory=lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://core.example", timeout=timeout),
    )
    return dispatcher, store, connection


def test_persisted_canonical_event_is_delivered_with_channel_key_only(monkeypatch, tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"accepted": True})

    dispatcher, store, connection = _bound_dispatcher(monkeypatch, tmp_path, handler)
    event = process_instagram_webhook(
        {
            "object": "instagram",
            "entry": [{
                "id": "178400012345678",
                "messaging": [{
                    "sender": {"id": "instagram_user_abc"},
                    "recipient": {"id": "178400012345678"},
                    "timestamp": 1710000000,
                    "message": {"mid": "mid-1", "text": "hola"},
                }],
            }],
        },
        request_id="request-1",
        connections=dispatcher._connections,
    )[0]
    persisted, created = dispatcher.persist(event)
    assert created and persisted["status"] == "pending"
    assert asyncio.run(dispatcher.dispatch_due()) == 1
    assert store.get(persisted["id"])["status"] == "delivered"
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/webhook/inbound"
    assert requests[0].headers["authorization"] == "Bearer channel-api-key-a"
    assert requests[0].headers["x-botly-contract-version"] == "canonical-v1"
    assert json.loads(requests[0].content) == event
    assert b"meta-access-token-must-not-leave-gateway" not in requests[0].content


def test_dedupes_event_id_but_scopes_provider_message_ids_by_account(tmp_path) -> None:
    store = CoreInboundDeliveryStore(tmp_path / "deliveries.json")
    first, created = store.enqueue(event=_event(), core_channel_id="core-a")
    repeated, created_again = store.enqueue(event=_event(), core_channel_id="core-a")
    second, second_created = store.enqueue(
        event=_event(event_id="event-2", account_id="178400099999999", connection_id="connection-2", message_id="mid-1"),
        core_channel_id="core-b",
    )
    assert created and not created_again and repeated["id"] == first["id"]
    assert second_created and second["id"] != first["id"]
    assert len(store.list()) == 2


def test_connection_delivery_projection_has_trace_evidence_without_credentials(monkeypatch, tmp_path) -> None:
    dispatcher, _store, connection = _bound_dispatcher(monkeypatch, tmp_path, lambda _request: httpx.Response(202))
    event = _event(connection_id=connection.id)
    event["message"]["attachments"] = [{"kind": "image", "providerMediaId": "media-1", "url": "https://private.example/media", "metadata": {"not": "public"}}]
    dispatcher.persist(event)

    item = dispatcher.list_connection_deliveries(connection.id)[0]
    assert item["event_id"] == "event-1"
    assert item["provider_message_id"] == "mid-1"
    assert item["sender_external_id"] == "instagram_user_abc"
    assert item["request_id"] == "request-1"
    assert item["attachments"] == [{"kind": "image", "providerMediaId": "media-1", "mimeType": None, "fileName": None, "size": None}]
    assert "meta-access-token-must-not-leave-gateway" not in json.dumps(item)
    assert "private.example" not in json.dumps(item)


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_core_client_errors_are_permanent(monkeypatch, tmp_path, status) -> None:
    dispatcher, store, connection = _bound_dispatcher(monkeypatch, tmp_path, lambda _request: httpx.Response(status))
    delivery, _ = dispatcher.persist(_event(connection_id=connection.id))
    asyncio.run(dispatcher.dispatch_due())
    assert store.get(delivery["id"])["status"] == "failed"


def test_core_server_error_timeout_and_duplicate_are_classified(monkeypatch, tmp_path) -> None:
    dispatcher, store, connection = _bound_dispatcher(monkeypatch, tmp_path, lambda _request: httpx.Response(500))
    retry, _ = dispatcher.persist(_event(connection_id=connection.id))
    asyncio.run(dispatcher.dispatch_due())
    assert store.get(retry["id"])["status"] == "retry"

    timeout_dir = tmp_path / "timeout"
    timeout_dir.mkdir()
    dispatcher, store, connection = _bound_dispatcher(monkeypatch, timeout_dir, lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout")))
    timeout, _ = dispatcher.persist(_event(connection_id=connection.id))
    asyncio.run(dispatcher.dispatch_due())
    assert store.get(timeout["id"])["status"] == "retry"

    duplicate_dir = tmp_path / "duplicate"
    duplicate_dir.mkdir()
    dispatcher, store, connection = _bound_dispatcher(monkeypatch, duplicate_dir, lambda _request: httpx.Response(409))
    duplicate, _ = dispatcher.persist(_event(connection_id=connection.id))
    asyncio.run(dispatcher.dispatch_due())
    completed = store.get(duplicate["id"])
    assert completed["status"] == "delivered" and completed["duplicateAcknowledged"] is True


def test_pending_delivery_is_recovered_after_restart(monkeypatch, tmp_path) -> None:
    delivered: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        delivered.append(request)
        return httpx.Response(200)

    dispatcher, store, connection = _bound_dispatcher(monkeypatch, tmp_path, handler)
    pending, _ = dispatcher.persist(_event(connection_id=connection.id))
    restarted_store = CoreInboundDeliveryStore(tmp_path / "deliveries.json")
    restarted = CoreInboundDispatcher(
        store=restarted_store,
        connections=dispatcher._connections,
        credentials=dispatcher._credentials,
        settings_factory=dispatcher._settings_factory,
        client_factory=dispatcher._client_factory,
    )
    assert asyncio.run(restarted.dispatch_due()) == 1
    assert restarted_store.get(pending["id"])["status"] == "delivered"
    assert len(delivered) == 1
