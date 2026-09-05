from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import meta_webhook
from app.services.clients import ClientService
from app.services.connection_registry import ConnectionRegistry
from app.services.connections import ConnectionService, UnsupportedConnectionProviderError
from app.services.credential_manager import CredentialManager, ProviderAccountReference
from app.services.gateway_settings import GatewaySettingsService
from app.services.instagram_webhook import InstagramWebhookError, process_instagram_webhook
import app.services.credential_manager as credential_manager_module
import app.services.instagram_webhook as instagram_webhook_module


class _Runtime:
    async def list_instances(self):
        return []


def _settings():
    return SimpleNamespace(
        meta_webhook_verify_token="verify-token",
        meta_webhook_require_signature=True,
        meta_app_secret="app-secret",
        instagram_app_secret="instagram-app-secret",
        bot_webhook_max_queue=200,
    )


def _bound_service(monkeypatch, tmp_path):
    monkeypatch.setattr(
        credential_manager_module,
        "get_settings",
        lambda: SimpleNamespace(
            official_credentials_path=str(tmp_path / "credentials.json"),
            official_credentials_encryption_key="",
            provider_credentials_encryption_key="provider-test-key",
            gateway_api_key="gateway-key",
            environment="test",
        ),
    )
    settings = GatewaySettingsService(tmp_path / "gateway_settings.json")
    settings.update_channels({"instagram": True})
    registry = ConnectionRegistry(tmp_path / "connections.json")
    client = ClientService(registry).create_client("Tenant A")
    service = ConnectionService(_Runtime(), registry, settings, CredentialManager())
    connection = service.create_connection(client_id=client.id, channel="instagram", provider="meta")
    account = ProviderAccountReference("meta", "instagram", "178400012345678")
    service._credentials.upsert_provider_credentials(
        account=account,
        access_token="credential-token",
        access_token_ref="meta://instagram/178400012345678/token",
        source="test",
        scopes=("instagram_business_basic",),
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    )
    service.bind_instagram_provider_account(connection_id=connection.id, account=account, metadata={"username": "botly"}, required_scopes=())
    return service, connection


def _payload(event: dict) -> dict:
    return {"object": "instagram", "entry": [{"id": "178400012345678", "messaging": [event]}]}


def _signed(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(b"instagram-app-secret", body, hashlib.sha256).hexdigest()
    return body, signature


def test_instagram_canonical_text_preserves_opaque_external_ids(monkeypatch, tmp_path) -> None:
    service, connection = _bound_service(monkeypatch, tmp_path)
    event = process_instagram_webhook(
        _payload({
            "sender": {"id": "instagram_user_abc"},
            "recipient": {"id": "178400012345678"},
            "timestamp": 1710000000,
            "message": {"mid": "ig-mid-001", "text": "Hola Ñ"},
        }),
        request_id="request-1",
        connections=service,
    )[0]

    assert event["eventType"] == "message.created"
    assert event["transport"] == {
        "provider": "meta", "channelType": "instagram", "connectionRef": connection.id, "providerAccountRef": "178400012345678"
    }
    assert event["message"]["sender"]["externalId"] == "instagram_user_abc"
    assert event["message"]["recipient"]["externalId"] == "178400012345678"
    assert event["message"]["providerMessageId"] == "ig-mid-001"
    assert event["message"]["content"] == "Hola Ñ"
    assert event["occurredAt"] == "2024-03-09T16:00:00Z"
    assert event["trace"]["requestId"] == "request-1"
    assert "credential-token" not in json.dumps(event)
    assert "remoteJid" not in json.dumps(event)


def test_instagram_canonical_attachment_reaction_echo_and_unsupported(monkeypatch, tmp_path) -> None:
    service, _ = _bound_service(monkeypatch, tmp_path)
    attachment = process_instagram_webhook(
        _payload({"sender": {"id": "user"}, "recipient": {"id": "178400012345678"}, "message": {"mid": "mid-media", "attachments": [{"type": "image", "payload": {"id": "media-1", "url": "https://cdn.example/image.jpg", "mime_type": "image/jpeg", "size": "12"}}]}}),
        request_id="request", connections=service,
    )[0]
    reaction = process_instagram_webhook(
        _payload({"sender": {"id": "user"}, "recipient": {"id": "178400012345678"}, "reaction": {"mid": "mid-media", "emoji": "❤️"}}),
        request_id="request", connections=service,
    )[0]
    echo = process_instagram_webhook(
        _payload({"sender": {"id": "178400012345678"}, "recipient": {"id": "user"}, "message": {"mid": "echo", "is_echo": True, "text": "self"}}),
        request_id="request", connections=service,
    )
    unsupported = process_instagram_webhook(
        _payload({"sender": {"id": "user"}, "recipient": {"id": "178400012345678"}, "typing": {"status": "on"}}),
        request_id="request", connections=service,
    )

    assert attachment["message"]["kind"] == "image"
    assert attachment["message"]["attachments"][0]["providerMediaId"] == "media-1"
    assert reaction["eventType"] == "message.reaction"
    assert reaction["message"]["content"] == "❤️"
    assert echo == ()
    assert unsupported == ()


def test_instagram_resolution_rejects_unknown_or_disconnected_connection(monkeypatch, tmp_path) -> None:
    service, connection = _bound_service(monkeypatch, tmp_path)
    unknown = _payload({"sender": {"id": "user"}, "message": {"mid": "m", "text": "hello"}})
    unknown["entry"][0]["id"] = "unknown-account"
    with pytest.raises(InstagramWebhookError, match="cannot receive") as exc:
        process_instagram_webhook(unknown, request_id="request", connections=service)
    assert exc.value.status_code == 404
    assert exc.value.provider_account_id == "unknown-account"
    assert exc.value.connection_lookup_result == "not_found"

    service.disconnect_instagram_connection(connection.id)
    with pytest.raises(InstagramWebhookError, match="cannot receive") as exc:
        process_instagram_webhook(_payload({"sender": {"id": "user"}, "message": {"mid": "m", "text": "hello"}}), request_id="request", connections=service)
    assert exc.value.status_code == 404


def test_meta_webhook_instagram_uses_raw_body_signature_and_acknowledges(monkeypatch, tmp_path) -> None:
    service, connection = _bound_service(monkeypatch, tmp_path)
    persisted: list[dict] = []
    timeline_events: list[dict] = []

    class _Dispatcher:
        def persist_many(self, events):
            persisted.extend(events)
            return list(events)

    monkeypatch.setattr(meta_webhook, "get_settings", _settings)
    monkeypatch.setattr(instagram_webhook_module, "get_connection_service", lambda: service)
    monkeypatch.setattr(meta_webhook, "get_core_inbound_dispatcher", lambda: _Dispatcher())
    monkeypatch.setattr(meta_webhook, "save_event", lambda event: timeline_events.append(event) or True)
    payload = _payload({"sender": {"id": "instagram_user_abc"}, "recipient": {"id": "178400012345678"}, "message": {"mid": "mid-1", "text": "hola"}})
    body, signature = _signed(payload)

    client = TestClient(app)
    response = client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": signature})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "object": "instagram", "canonicalEvents": 1, "acknowledged": 1}
    assert len(persisted) == 1 and persisted[0]["trace"]["correlationId"]
    assert timeline_events[0]["instance"] == connection.id
    assert timeline_events[0]["sender"] == "instagram_user_abc"
    assert timeline_events[0]["message"]["id"] == "mid-1"
    assert timeline_events[0]["providerDelivery"]["channelId"] == "instagram"
    assert "remoteJid" not in json.dumps(timeline_events[0])
    assert "credential-token" not in json.dumps(timeline_events[0])

    modified = body.replace(b"hola", b"chau")
    assert client.post("/webhooks/meta", content=modified, headers={"X-Hub-Signature-256": signature}).status_code == 401
    assert client.post("/webhooks/meta", content=body, headers={}).status_code == 401
    assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    assert connection.id


def test_instagram_resolution_rejects_invalid_or_ambiguous_persisted_bindings(monkeypatch, tmp_path) -> None:
    service, connection = _bound_service(monkeypatch, tmp_path)
    record = service._registry.connection_record_by_id(connection.id)
    assert record is not None
    record["provider_id"] = "evolution"
    service._registry.save_connection_record(str(record["legacy_name"]), record)
    with pytest.raises(InstagramWebhookError) as wrong_provider:
        process_instagram_webhook(
            _payload({"sender": {"id": "user"}, "message": {"mid": "m", "text": "hello"}}),
            request_id="request",
            connections=service,
        )
    assert wrong_provider.value.status_code == 409
    assert wrong_provider.value.provider_account_id == "178400012345678"
    assert wrong_provider.value.connection_lookup_result == "invalid_binding"
    service._registry.update_connection_record(connection.id, {"provider_id": "meta", "channel_id": "whatsapp"})
    with pytest.raises(InstagramWebhookError) as wrong_channel:
        process_instagram_webhook(
            _payload({"sender": {"id": "user"}, "message": {"mid": "m", "text": "hello"}}),
            request_id="request",
            connections=service,
        )
    assert wrong_channel.value.status_code == 409

    duplicate_path = tmp_path / "duplicate"
    duplicate_path.mkdir()
    service, connection = _bound_service(monkeypatch, duplicate_path)
    record = service._registry.connection_record_by_id(connection.id)
    assert record is not None
    duplicate = dict(record)
    duplicate["id"] = "duplicate-connection"
    duplicate["legacy_name"] = "duplicate-connection"
    service._registry.save_connection_record(str(duplicate["legacy_name"]), duplicate)
    with pytest.raises(InstagramWebhookError) as duplicate_binding:
        process_instagram_webhook(
            _payload({"sender": {"id": "user"}, "message": {"mid": "m", "text": "hello"}}),
            request_id="request",
            connections=service,
        )
    assert duplicate_binding.value.status_code == 409


def test_instagram_binding_cannot_be_reused_by_another_tenant(monkeypatch, tmp_path) -> None:
    service, _ = _bound_service(monkeypatch, tmp_path)
    other = ClientService(service._registry).create_client("Tenant B")
    connection = service.create_connection(client_id=other.id, channel="instagram", provider="meta")
    with pytest.raises(UnsupportedConnectionProviderError, match="already bound"):
        service.bind_instagram_provider_account(
            connection_id=connection.id,
            account=ProviderAccountReference("meta", "instagram", "178400012345678"),
            metadata={},
            required_scopes=(),
        )


def test_instagram_webhook_rejects_malformed_structure_without_500(monkeypatch, tmp_path) -> None:
    service, _ = _bound_service(monkeypatch, tmp_path)
    monkeypatch.setattr(meta_webhook, "get_settings", _settings)
    monkeypatch.setattr(instagram_webhook_module, "get_connection_service", lambda: service)
    body, signature = _signed({"object": "instagram", "entry": "not-a-list"})
    response = TestClient(app).post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": signature})
    assert response.status_code == 400


def test_meta_signature_uses_constant_time_compare_and_app_secret_only(monkeypatch) -> None:
    compared: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def compare(actual: str, expected: str) -> bool:
        compared.append((actual, expected))
        return original(actual, expected)

    monkeypatch.setattr(meta_webhook.hmac, "compare_digest", compare)
    body = b'{"object":"instagram"}'
    signature = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert meta_webhook._signature_is_valid(body, signature, "app-secret")
    assert not meta_webhook._signature_is_valid(body, signature, "wrong-app-secret")
    assert compared


def test_meta_webhook_challenge_accepts_only_configured_token(monkeypatch) -> None:
    monkeypatch.setattr(meta_webhook, "get_settings", _settings)
    client = TestClient(app)
    valid = client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "verify-token", "hub.challenge": "challenge"})
    assert valid.status_code == 200 and valid.text == "challenge"
    assert client.get("/webhooks/meta", params={"hub.mode": "unsubscribe", "hub.verify_token": "verify-token", "hub.challenge": "challenge"}).status_code == 403
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "challenge"}).status_code == 403
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "verify-token"}).status_code == 403
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "verify-token", "hub.challenge": ""}).status_code == 403
