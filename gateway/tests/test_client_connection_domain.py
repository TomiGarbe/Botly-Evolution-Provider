from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.requests import CreateClientRequest, UpdateClientRequest
from app.core.config import Settings
from app.routers import clients as clients_router
from app.routers import connections as connections_router
from app.services import connection_registry as connection_registry_module
from app.services.clients import ClientHasConnectionsError, ClientService
from app.services.connection_registry import ConnectionRegistry
from app.services.connections import ConnectionService
from app.services.connection_operations import ConnectionOperationsService


def test_connection_registry_default_path_is_persistent() -> None:
    settings = Settings(gateway_api_key="test-gateway-key", evolution_api_key="test-evolution-key")

    assert settings.connection_registry_path == "/var/lib/botly/connections/connection_registry.json"


def test_connection_registry_uses_configured_path_and_reloads_data(monkeypatch, tmp_path) -> None:
    persistent_path = tmp_path / "nested" / "connections" / "connection_registry.json"
    monkeypatch.setattr(
        connection_registry_module,
        "get_settings",
        lambda: SimpleNamespace(connection_registry_path=str(persistent_path)),
    )

    first = ConnectionRegistry()
    client = first.save_client({"id": "client-01", "name": "Global Tech"})
    second = ConnectionRegistry()

    assert persistent_path.exists()
    assert persistent_path.parent.is_dir()
    assert second.get_client(client["id"]) == client
    assert str(second._path) == str(persistent_path)
    assert "/tmp/botly_connection_registry.json" not in str(second._path)


def test_connection_registry_keeps_data_when_reinitialized_at_another_configured_path(monkeypatch, tmp_path) -> None:
    first_path = tmp_path / "first" / "connection_registry.json"
    second_path = tmp_path / "second" / "connection_registry.json"
    configured_path = {"value": first_path}
    monkeypatch.setattr(
        connection_registry_module,
        "get_settings",
        lambda: SimpleNamespace(connection_registry_path=str(configured_path["value"])),
    )

    first = ConnectionRegistry()
    first.save_client({"id": "client-01", "name": "Global Tech"})
    configured_path["value"] = second_path
    second = ConnectionRegistry()
    second.save_client({"id": "client-02", "name": "Another client"})

    assert ConnectionRegistry(first_path).get_client("client-01") is not None
    assert ConnectionRegistry(second_path).get_client("client-02") is not None


def test_connection_registry_copies_legacy_tmp_store_without_deleting_it(monkeypatch, tmp_path) -> None:
    legacy_path = tmp_path / "legacy" / "botly_connection_registry.json"
    persistent_path = tmp_path / "durable" / "connections" / "connection_registry.json"
    legacy = ConnectionRegistry(legacy_path)
    legacy.save_client({"id": "client-01", "name": "Global Tech"})
    monkeypatch.setattr(connection_registry_module, "_LEGACY_CONNECTION_REGISTRY_PATH", legacy_path)
    monkeypatch.setattr(
        connection_registry_module,
        "get_settings",
        lambda: SimpleNamespace(connection_registry_path=str(persistent_path)),
    )

    migrated = ConnectionRegistry()

    assert legacy_path.exists()
    assert persistent_path.exists()
    assert migrated.get_client("client-01") == {"id": "client-01", "name": "Global Tech"}


class _LegacyRuntime:
    async def list_instances(self) -> list[dict]:
        return [
            {
                "instanceName": "acme_support",
                "instanceId": "connection-01",
                "integration": "WHATSAPP-BUSINESS",
                "connectionStatus": "open",
                "profileName": "Acme",
                "number": "5491100000000",
            },
            {
                "instanceName": "acme_web",
                "integration": "WHATSAPP-BAILEYS",
                "connectionStatus": "connecting",
            },
        ]


class _StaleLegacyRuntime:
    """Models a provider that still lists an instance after accepting DELETE."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def list_instances(self) -> list[dict]:
        return [{"instanceName": "migrated_support", "instanceId": "legacy-connection-01", "integration": "WHATSAPP-BAILEYS", "connectionStatus": "open"}]

    async def delete(self, name: str) -> dict:
        self.deleted.append(name)
        return {"ok": True}


def test_client_service_persists_simple_client_model(tmp_path) -> None:
    service = ClientService(ConnectionRegistry(tmp_path / "connection_registry.json"))

    created = service.create_client("Global Tech", "Cliente de prueba")
    updated = service.update_client(created.id, description=None)

    assert updated.id == created.id
    assert updated.name == "Global Tech"
    assert updated.description is None
    assert service.list_clients() == [updated]

    service.delete_client(created.id)
    assert service.list_clients() == []


def test_client_overview_reports_connection_count_and_latest_activity(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    service = ClientService(registry)
    client = service.create_client("Global Tech")
    registry.save_connection_record(
        "global-tech-support",
        {
            "id": "connection-01",
            "legacy_name": "global-tech-support",
            "client_id": client.id,
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T00:00:00Z",
            "last_activity_at": "2026-08-02T09:00:00Z",
        },
    )
    registry.save_connection_record(
        "global-tech-sales",
        {
            "id": "connection-02",
            "legacy_name": "global-tech-sales",
            "client_id": client.id,
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T00:00:00Z",
            "last_activity_at": "2026-08-03T09:00:00Z",
        },
    )

    overview = service.get_client_overview(client.id)

    assert overview.connection_count == 2
    assert overview.last_activity_at == "2026-08-03T09:00:00Z"
    with pytest.raises(ClientHasConnectionsError):
        service.delete_client(client.id)


def test_client_requests_trim_values_and_reject_invalid_updates() -> None:
    created = CreateClientRequest(name="  Global Tech  ", description="  Cliente principal  ")

    assert created.name == "Global Tech"
    assert created.description == "Cliente principal"

    with pytest.raises(ValidationError):
        CreateClientRequest(name="   ")
    with pytest.raises(ValidationError):
        UpdateClientRequest()
    with pytest.raises(ValidationError):
        UpdateClientRequest(name=None)
    with pytest.raises(ValidationError):
        CreateClientRequest(name="Global Tech", unexpected="value")


def test_clients_router_exposes_complete_crud_contract(monkeypatch, tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    service = ClientService(registry)
    monkeypatch.setattr(clients_router, "_service", service)
    api = FastAPI()
    api.include_router(clients_router.router)
    http = TestClient(api)

    created = http.post("/clients", json={"name": "  Global Tech  ", "description": "  Cliente principal  "})
    assert created.status_code == 201
    assert created.json()["name"] == "Global Tech"
    assert created.json()["connection_count"] == 0
    client_id = created.json()["id"]

    listed = http.get("/clients")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [client_id]

    fetched = http.get(f"/clients/{client_id}")
    assert fetched.status_code == 200
    assert fetched.json()["description"] == "Cliente principal"

    patched = http.patch(f"/clients/{client_id}", json={"description": None})
    assert patched.status_code == 200
    assert patched.json()["description"] is None

    replaced = http.put(f"/clients/{client_id}", json={"name": "Global Tech Argentina"})
    assert replaced.status_code == 200
    assert replaced.json()["name"] == "Global Tech Argentina"

    invalid = http.post("/clients", json={"name": " ", "extra": "not-allowed"})
    assert invalid.status_code == 422

    deleted = http.delete(f"/clients/{client_id}")
    assert deleted.status_code == 204
    assert http.get(f"/clients/{client_id}").status_code == 404

    connected = service.create_client("Connected client")
    registry.save_connection_record(
        "connected-client",
        {"id": "connection-03", "legacy_name": "connected-client", "client_id": connected.id},
    )
    assert http.delete(f"/clients/{connected.id}").status_code == 409


class _EmptyRuntime:
    async def list_instances(self) -> list[dict]:
        return []

    async def delete(self, _name: str) -> dict:
        return {"ok": True}


class _MessagingRuntime(_EmptyRuntime):
    def __init__(self, runtime_name: str) -> None:
        self.runtime_name = runtime_name
        self.sent: list[tuple[str, str, str]] = []

    async def list_instances(self) -> list[dict]:
        return [{"name": self.runtime_name, "status": "open"}]

    async def send_text(self, instance_name: str, number: str, text: str) -> dict:
        self.sent.append((instance_name, number, text))
        return {"messageId": "message-01"}

    async def reconnect(self, _name: str) -> dict:
        return {"ok": True}


def test_connection_name_update_is_retained_when_runtime_is_available(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    client = ClientService(registry).create_client("Global Tech")
    connection = ConnectionService(_EmptyRuntime(), registry).create_connection(client_id=client.id, channel="whatsapp")
    runtime_name = registry.connection_record_by_id(connection.id)["legacy_name"]
    service = ConnectionService(_MessagingRuntime(runtime_name), registry)

    updated = asyncio.run(service.update_connection(connection.id, name="WhatsApp soporte"))

    assert updated.name == "WhatsApp soporte"
    assert asyncio.run(service.get_connection(connection.id)).name == "WhatsApp soporte"


def test_connections_router_exposes_client_bound_crud_contract(monkeypatch, tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    clients = ClientService(registry)
    owner = clients.create_client("Global Tech")
    service = ConnectionService(_EmptyRuntime(), registry)
    monkeypatch.setattr(connections_router, "_service", service)
    api = FastAPI()
    api.include_router(connections_router.router)
    http = TestClient(api)

    created = http.post("/connections", json={"client_id": owner.id, "channel": "whatsapp"})
    assert created.status_code == 201
    assert created.json()["client_id"] == owner.id
    assert created.json()["client"]["name"] == "Global Tech"
    assert created.json()["channel"]["id"] == "whatsapp"
    assert created.json()["status"]["state"] == "pending"
    connection_id = created.json()["id"]
    assert clients.get_client_overview(owner.id).connection_count == 1

    listed = http.get(f"/connections?client_id={owner.id}")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [connection_id]

    updated = http.patch(f"/connections/{connection_id}", json={"name": "WhatsApp soporte"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "WhatsApp soporte"

    assert http.post("/connections", json={"client_id": "missing", "channel": "whatsapp"}).status_code == 404
    assert http.post("/connections", json={"client_id": owner.id, "channel": "instagram"}).status_code == 422
    assert http.patch(f"/connections/{connection_id}", json={}).status_code == 422

    deleted = http.delete(f"/connections/{connection_id}")
    assert deleted.status_code == 204
    assert http.get(f"/connections/{connection_id}").status_code == 404
    assert http.get(f"/connections?client_id={owner.id}").json() == []


def test_connection_messages_use_the_connection_address_for_timeline_and_send(monkeypatch, tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    owner = ClientService(registry).create_client("Global Tech")
    service = ConnectionService(_EmptyRuntime(), registry)
    connection = service.create_connection(client_id=owner.id, channel="whatsapp")
    sent: dict[str, str] = {}

    class Operations:
        async def send_quick_message(self, connection_id: str, *, number: str, text: str) -> dict[str, str]:
            sent.update(connection_id=connection_id, number=number, text=text)
            return {"ok": "true"}

    monkeypatch.setattr(connections_router, "_service", service)
    monkeypatch.setattr(connections_router, "_operations", Operations())
    monkeypatch.setattr(connections_router, "list_logical_messages", lambda instance, limit: [{"instance": instance, "limit": limit}])
    api = FastAPI()
    api.include_router(connections_router.router)
    http = TestClient(api)

    timeline = http.get(f"/connections/{connection.id}/messages")
    assert timeline.status_code == 200
    assert timeline.json()["items"][0]["instance"] == service.connection_runtime_name(connection.id)

    delivered = http.post(f"/connections/{connection.id}/messages", json={"recipient": "5491112345678", "text": "Hola"})
    assert delivered.status_code == 200
    assert sent == {"connection_id": connection.id, "number": "5491112345678", "text": "Hola"}


def test_connection_messages_route_an_instagram_connection_without_a_frontend_branch(monkeypatch) -> None:
    sent: dict[str, str] = {}

    class InstagramService:
        connection = SimpleNamespace(
            client_id="client-01",
            channel=SimpleNamespace(id="instagram"),
            provider=SimpleNamespace(id="meta"),
        )

        async def get_connection(self, connection_id: str):
            assert connection_id == "instagram-01"
            return self.connection

        async def send_instagram_text(self, *, connection_id: str, external_id: str, text: str) -> dict[str, str]:
            sent.update(connection_id=connection_id, external_id=external_id, text=text)
            return {"ok": "true"}

    monkeypatch.setattr(connections_router, "_service", InstagramService())
    monkeypatch.setattr(connections_router, "list_logical_messages", lambda instance, limit: [{"instance": instance, "limit": limit}])
    api = FastAPI()
    api.include_router(connections_router.router)
    http = TestClient(api)

    timeline = http.get("/connections/instagram-01/messages")
    assert timeline.status_code == 200
    assert timeline.json()["items"][0]["instance"] == "instagram-01"

    delivered = http.post("/connections/instagram-01/messages", json={"recipient": "opaque-user-id", "text": "Hola"})
    assert delivered.status_code == 200
    assert sent == {"connection_id": "instagram-01", "external_id": "opaque-user-id", "text": "Hola"}


def test_empty_runtime_does_not_create_a_migration_client(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    service = ConnectionService(_EmptyRuntime(), registry)

    assert asyncio.run(service.migrate_legacy_connections()) == 0
    assert registry.list_clients() == []


def test_connection_operations_are_scoped_to_the_connection(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        instance_webhooks_path=str(tmp_path / "webhooks.json"),
        instance_api_keys_path=str(tmp_path / "api_keys.json"),
        gateway_api_key="test-gateway-key",
        instance_webhooks_encryption_key="test-webhook-encryption-key",
        webhook_dispatch_history_limit=30,
    )
    monkeypatch.setattr("app.services.instance_webhooks.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.instance_auth.get_settings", lambda: settings)

    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    client = ClientService(registry).create_client("Global Tech")
    connection = ConnectionService(_EmptyRuntime(), registry).create_connection(client_id=client.id, channel="whatsapp")
    operations = ConnectionOperationsService(_EmptyRuntime(), registry)

    assert operations.webhook(connection.id)["configured"] is False
    webhook = operations.update_webhook(connection.id, "https://example.test/webhooks/botly")
    assert webhook["configured"] is True
    assert webhook["enabled"] is True
    assert webhook["url"] == "https://example.test/webhooks/botly"
    assert webhook["successful_deliveries"] == 0
    assert webhook["failed_deliveries"] == 0

    api_key = operations.api_key(connection.id)
    regenerated = operations.regenerate_api_key(connection.id)
    assert api_key["has_api_key"] is True
    assert regenerated["api_key"].startswith("inst_")
    assert regenerated["can_reveal_api_key"] is True
    revealed = operations.api_key(connection.id, reveal=True)
    assert revealed["api_key"] == regenerated["api_key"]
    assert operations.recent_activity(connection.id) == []


def test_instagram_connection_api_key_is_persistent_and_supported(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        instance_api_keys_path=str(tmp_path / "api_keys.json"),
        gateway_api_key="test-gateway-key",
    )
    monkeypatch.setattr("app.services.instance_auth.get_settings", lambda: settings)

    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    client = ClientService(registry).create_client("Global Tech")
    service = ConnectionService(_EmptyRuntime(), registry)
    created = service.create_connection(client_id=client.id, channel="whatsapp")
    registry.update_connection_record(
        created.id,
        {"provider_id": "meta", "channel_id": "instagram", "channel_display_name": "Instagram"},
    )

    instagram = asyncio.run(service.get_connection(created.id))
    operations = ConnectionOperationsService(_EmptyRuntime(), registry)
    generated = operations.regenerate_api_key(instagram.id)
    reloaded = operations.api_key(instagram.id, reveal=True)

    assert instagram.capabilities.supports_api_key is True
    assert reloaded["api_key"] == generated["api_key"]


def test_connection_operations_record_quick_message_and_heartbeat(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    client = ClientService(registry).create_client("Global Tech")
    connection = ConnectionService(_EmptyRuntime(), registry).create_connection(client_id=client.id, channel="whatsapp")
    runtime_name = registry.connection_record_by_id(connection.id)["legacy_name"]
    runtime = _MessagingRuntime(runtime_name)
    operations = ConnectionOperationsService(runtime, registry)

    result = asyncio.run(operations.send_quick_message(connection.id, number="+54 9 11 0000 0000", text="Hola"))
    status = asyncio.run(operations.status(connection.id))
    activity = operations.recent_activity(connection.id)

    assert result["ok"] is True
    assert runtime.sent == [(runtime_name, "5491100000000", "Hola")]
    assert status["connected"] is True
    assert status["last_heartbeat_at"] is not None
    assert activity[0]["description"] == "Mensaje enviado"
    assert activity[0]["technical"]["Componente"] == "Mensajería"


def test_connection_integration_endpoints_are_addressed_by_connection(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.connection_operations.get_settings",
        lambda: SimpleNamespace(public_app_url="https://gateway.example.test", gateway_port=9000),
    )
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    client = ClientService(registry).create_client("Global Tech")
    connection = ConnectionService(_EmptyRuntime(), registry).create_connection(client_id=client.id, channel="whatsapp")
    endpoints = ConnectionOperationsService(_EmptyRuntime(), registry).integration_endpoints(connection.id)

    assert endpoints["message_api_url"] == f"https://gateway.example.test/connections/{connection.id}/messages"
    assert endpoints["meta_webhook_url"] == "https://gateway.example.test/webhooks/meta"


def test_legacy_connections_receive_a_migration_client_without_runtime_mutation(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    clients = ClientService(registry)
    service = ConnectionService(_LegacyRuntime(), registry)

    connections = asyncio.run(service.list_connections())

    assert len(connections) == 2
    assert {connection.client_id for connection in connections}
    assert connections[0].id == "connection-01"
    assert connections[0].status.state == "connected"
    assert connections[0].provider.id == "meta"
    assert connections[0].channel.id == "whatsapp"
    assert connections[0].capabilities.supports_official_api is True
    assert connections[1].capabilities.supports_qr is True

    migrated_client = clients.get_client(connections[0].client_id)
    assert migrated_client.name == "Migrated connections"
    with pytest.raises(ClientHasConnectionsError):
        clients.delete_client(migrated_client.id)


def test_deleted_migrated_connection_is_not_reimported_after_a_fresh_registry_read(tmp_path) -> None:
    path = tmp_path / "connection_registry.json"
    runtime = _StaleLegacyRuntime()
    service = ConnectionService(runtime, ConnectionRegistry(path))

    migrated = asyncio.run(service.list_connections())
    assert [connection.id for connection in migrated] == ["legacy-connection-01"]

    asyncio.run(service.delete_connection("legacy-connection-01"))

    assert runtime.deleted == ["migrated_support"]
    assert ConnectionRegistry(path).connection_record_by_id("legacy-connection-01") is None
    assert ConnectionRegistry(path).snapshot()["deleted_legacy_names"] == {
        "migrated_support": {"connection_id": "legacy-connection-01"}
    }

    # This fresh registry/service pair models both a list reload and a restart.
    reloaded = ConnectionService(runtime, ConnectionRegistry(path))
    assert asyncio.run(reloaded.migrate_legacy_connections()) == 0
    assert asyncio.run(reloaded.list_connections()) == []


def test_registry_migration_is_idempotent_and_snapshot_is_reversible(tmp_path) -> None:
    registry = ConnectionRegistry(tmp_path / "connection_registry.json")
    service = ConnectionService(_LegacyRuntime(), registry)

    assert asyncio.run(service.migrate_legacy_connections()) == 2
    snapshot = registry.snapshot()
    assert asyncio.run(service.migrate_legacy_connections()) == 0

    registry.replace({"clients": {}, "connections": {}})
    assert registry.list_clients() == []
    registry.replace(snapshot)

    assert len(registry.connection_records()) == 2
