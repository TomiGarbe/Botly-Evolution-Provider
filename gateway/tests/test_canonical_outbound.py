from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import canonical_outbound


def _body(channel_id: str, *, recipient: str = "5491100000000") -> dict:
    return {
        "channel": {"id": channel_id, "type": "whatsapp", "provider": "evolution"},
        "recipient": {"externalId": recipient},
        "message": {"kind": "text", "content": "BOTLY-WA-OUTBOUND-002", "attachments": []},
        "idempotencyKey": "core:test:outbound-002",
        "metadata": {"source": "test"},
        "trace": {"requestId": "req-1", "correlationId": "corr-1"},
    }


class _Registry:
    def __init__(self, channel_id: str) -> None:
        self.record = {
            "legacy_name": "evolution_instance",
            "provider_id": "evolution",
            "channel_id": "whatsapp",
            "core_channel": {"channelId": channel_id},
        }

    def connection_record(self, name: str):
        return self.record if name == "evolution_instance" else None

    def connection_records(self):
        return [self.record]


class _AttemptStore:
    def create(self, **kwargs):
        self.created = kwargs
        return {"id": "attempt-1"}

    def finish_success(self, attempt_id, result):
        return {"id": attempt_id, "providerMessageId": result["key"]["id"]}

    def finish_error(self, attempt_id, exc):
        return {}


class _ConnectionManager:
    def __init__(self) -> None:
        self.calls = []

    async def send_text(self, instance: str, number: str, text: str):
        self.calls.append((instance, number, text))
        return {"key": {"id": "evolution-message-1"}}


def _client(monkeypatch, channel_id: str):
    registry = _Registry(channel_id)
    manager = _ConnectionManager()
    attempts = _AttemptStore()
    monkeypatch.setattr(canonical_outbound, "CanonicalOutboundService", lambda: __import__("app.services.canonical_outbound", fromlist=["CanonicalOutboundService"]).CanonicalOutboundService(registry=registry, connection_manager=manager))
    monkeypatch.setattr("app.services.canonical_outbound.get_outbound_provider_attempt_store", lambda: attempts)
    app = FastAPI()
    app.include_router(canonical_outbound.router)
    return TestClient(app), manager, attempts


def test_canonical_endpoint_maps_external_id_only_inside_gateway(monkeypatch) -> None:
    channel_id = str(uuid4())
    http, manager, attempts = _client(monkeypatch, channel_id)

    response = http.post("/v1/outbound/messages", json=_body(channel_id), headers={"X-Botly-Contract-Version": "canonical-v1"})

    assert response.status_code == 202
    assert manager.calls == [("evolution_instance", "5491100000000", "BOTLY-WA-OUTBOUND-002")]
    assert attempts.created["idempotency_key"] == "core:test:outbound-002"
    assert response.json()["providerMessageId"] == "evolution-message-1"


def test_canonical_endpoint_requires_version_header_and_never_requires_legacy_fields(monkeypatch) -> None:
    channel_id = str(uuid4())
    http, manager, _attempts = _client(monkeypatch, channel_id)

    missing_version = http.post("/v1/outbound/messages", json=_body(channel_id))
    valid = http.post("/v1/outbound/messages", json=_body(channel_id), headers={"X-Botly-Contract-Version": "canonical-v1"})

    assert missing_version.status_code == 422
    assert valid.status_code == 202
    assert manager.calls[0][1] == "5491100000000"


def test_canonical_endpoint_rejects_another_contract_version(monkeypatch) -> None:
    channel_id = str(uuid4())
    http, manager, _attempts = _client(monkeypatch, channel_id)

    response = http.post("/v1/outbound/messages", json=_body(channel_id), headers={"X-Botly-Contract-Version": "legacy-v1"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_contract_version"
    assert manager.calls == []


def test_canonical_endpoint_rejects_invalid_contract_before_provider_call(monkeypatch) -> None:
    channel_id = str(uuid4())
    http, manager, _attempts = _client(monkeypatch, channel_id)
    body = _body(channel_id)
    body["channel"]["provider"] = "invalid"

    response = http.post("/v1/outbound/messages", json=body, headers={"X-Botly-Contract-Version": "canonical-v1"})

    assert response.status_code == 422
    assert manager.calls == []
