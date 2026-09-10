from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain import ChannelId, ChannelStatus, MethodId, ProvisionedChannel, RuntimeId
from app.models.canonical_outbound import CanonicalOutboundMessage
from app.platforms.instagram import InstagramGraphClient, InstagramGraphError
from app.platforms.meta import MetaPlatformError
from app.providers.instagram import MetaInstagramProvider
from app.providers.registry import ProviderRegistry
from app.routers import canonical_outbound
from app.services.canonical_outbound import CanonicalOutboundError, CanonicalOutboundService
from app.services.connections import UnsupportedConnectionProviderError
from app.services.outbound_provider_attempts import OutboundProviderAttemptStore


def _instagram_settings():
    return SimpleNamespace(
        instagram_graph_api_url="https://graph.instagram.com",
        instagram_graph_api_version="v23.0",
        meta_signup_timeout_seconds=30,
    )


def _message(channel_id: str, *, recipient: str = "instagram:opaque-user/ABC-123", idempotency_key: str = "core:instagram:one") -> CanonicalOutboundMessage:
    return CanonicalOutboundMessage.model_validate({
        "channel": {"id": channel_id, "type": "instagram", "provider": "meta"},
        "recipient": {"externalId": recipient},
        "message": {"kind": "text", "content": "Hola desde Botly", "attachments": []},
        "idempotencyKey": idempotency_key,
        "metadata": {"source": "test"},
        "trace": {"requestId": "req-instagram", "correlationId": "corr-instagram"},
    })


class _Registry:
    def __init__(self, channel_id: str, *, provider: str = "meta", channel_type: str = "instagram") -> None:
        self.record = {
            "id": "connection-instagram-1",
            "legacy_name": "instagram_runtime",
            "provider_id": provider,
            "channel_id": channel_type,
            "core_channel": {"channelId": channel_id},
        }

    def connection_record(self, name: str):
        return self.record if name == "instagram_runtime" else None

    def connection_records(self):
        return [self.record]


class _InstagramConnectionService:
    def __init__(self, *, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {"providerMessageId": "ig-message-1"}
        self.error = error
        self.calls: list[dict] = []

    async def send_instagram_text(self, *, connection_id: str, external_id: str, text: str, provider: MetaInstagramProvider):
        if self.error:
            raise self.error
        self.calls.append({"connection_id": connection_id, "external_id": external_id, "text": text, "provider": provider})
        return self.result


def _provider_registry(adapter: MetaInstagramProvider | None = None) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(provider_id="meta", channel_type="instagram", adapter=adapter or MetaInstagramProvider())
    return registry


def _service(
    tmp_path,
    channel_id: str,
    connection_service: _InstagramConnectionService,
    *,
    registry: _Registry | None = None,
    provider_registry: ProviderRegistry | None = None,
    connection_manager=None,
) -> CanonicalOutboundService:
    return CanonicalOutboundService(
        registry=registry or _Registry(channel_id),
        connection_manager=connection_manager,
        connection_service=connection_service,
        provider_registry=provider_registry or _provider_registry(),
        attempt_store=OutboundProviderAttemptStore(tmp_path / "attempts.json"),
    )


def test_canonical_instagram_selects_meta_adapter_and_preserves_opaque_external_id(tmp_path) -> None:
    channel_id = str(uuid4())
    external_id = "opaque:instagram-user/not-a-phone"
    connection_service = _InstagramConnectionService()

    class _EvolutionTrap:
        calls: list[tuple] = []

        async def send_text(self, *args):
            self.calls.append(args)
            raise AssertionError("Instagram canonical delivery must not select Evolution")

    evolution = _EvolutionTrap()

    result = asyncio.run(_service(tmp_path, channel_id, connection_service, connection_manager=evolution).deliver(
        message=_message(channel_id, recipient=external_id), authenticated_instance=None,
    ))

    assert result["status"] == "accepted"
    assert result["providerMessageId"] == "ig-message-1"
    assert len(connection_service.calls) == 1
    call = connection_service.calls[0]
    assert call["external_id"] == external_id
    assert isinstance(call["provider"], MetaInstagramProvider)
    assert "number" not in call
    assert "remoteJid" not in call
    assert evolution.calls == []


def test_canonical_instagram_preserves_numeric_external_id_exactly(tmp_path) -> None:
    channel_id = str(uuid4())
    # Deliberate surrounding whitespace proves this is not coerced to a number
    # or normalized as a phone/JID on its way to the adapter.
    external_id = " 17840000000000000 "
    connection_service = _InstagramConnectionService()

    asyncio.run(_service(tmp_path, channel_id, connection_service).deliver(
        message=_message(channel_id, recipient=external_id), authenticated_instance=None,
    ))

    assert connection_service.calls[0]["external_id"] == external_id
    assert connection_service.calls[0]["external_id"].strip() == "17840000000000000"


def test_canonical_instagram_rejects_media_without_selecting_a_provider(tmp_path) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService()
    message = CanonicalOutboundMessage.model_validate({
        **_message(channel_id).model_dump(by_alias=True),
        "message": {
            "kind": "image",
            "content": "caption",
            "attachments": [{"kind": "image", "url": "https://example.test/image.jpg"}],
        },
    })

    with pytest.raises(CanonicalOutboundError) as raised:
        asyncio.run(_service(tmp_path, channel_id, connection_service).deliver(message=message, authenticated_instance=None))

    assert raised.value.status_code == 422
    assert raised.value.code == "message_kind_unsupported"
    assert connection_service.calls == []


def test_canonical_instagram_uses_provider_graph_adapter_and_maps_message_id(tmp_path) -> None:
    channel_id = str(uuid4())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"recipient_id": "opaque-recipient", "message_id": "ig-test-message-id"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://graph.instagram.com")
    adapter = MetaInstagramProvider(transport=InstagramGraphClient(client=client, settings_factory=_instagram_settings))

    class _AdapterBridge(_InstagramConnectionService):
        async def send_instagram_text(self, *, connection_id: str, external_id: str, text: str, provider: MetaInstagramProvider):
            self.calls.append({"connection_id": connection_id, "external_id": external_id, "text": text, "provider": provider})
            graph_result = await provider.send_text(
                channel=ProvisionedChannel(
                    id="connection:instagram-1",
                    channel_id=ChannelId.INSTAGRAM,
                    method_id=MethodId.OFFICIAL,
                    integration_id="instagram.official.meta",
                    runtime_id=RuntimeId.META,
                    display_name="Instagram",
                    status=ChannelStatus.ACTIVE,
                    metadata={"graphSendNodeId": "page-node-1"},
                ),
                recipient_id=external_id,
                text=text,
                access_token="test-token",
            )
            return {"providerMessageId": graph_result["message_id"]}

    connection_service = _AdapterBridge()
    result = asyncio.run(_service(
        tmp_path,
        channel_id,
        connection_service,
        provider_registry=_provider_registry(adapter),
    ).deliver(message=_message(channel_id, recipient="opaque-recipient"), authenticated_instance=None))
    asyncio.run(client.aclose())

    assert result["providerMessageId"] == "ig-test-message-id"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.host == "graph.instagram.com"
    assert requests[0].url.path == "/v23.0/page-node-1/messages"
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert "access_token" not in requests[0].url.params
    assert "graph.facebook.com" not in str(requests[0].url)
    assert json.loads(requests[0].content) == {
        "recipient": {"id": "opaque-recipient"},
        "message": {"text": "Hola desde Botly"},
    }


@pytest.mark.parametrize("error", [
    UnsupportedConnectionProviderError("Instagram credentials are unavailable"),
    RuntimeError("credential cannot be decrypted"),
])
def test_canonical_instagram_configuration_failures_are_safe_and_do_not_call_meta(tmp_path, error: Exception) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService(error=error)

    with pytest.raises(CanonicalOutboundError) as raised:
        asyncio.run(_service(tmp_path, channel_id, connection_service).deliver(
            message=_message(channel_id), authenticated_instance=None,
        ))

    assert raised.value.status_code == 422
    assert raised.value.code == "provider_configuration_invalid"
    assert connection_service.calls == []


@pytest.mark.parametrize("status_code", [400, 502])
def test_canonical_instagram_maps_meta_errors_without_exposing_details(tmp_path, status_code: int) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService(error=MetaPlatformError("provider detail must remain internal", status_code=status_code))

    with pytest.raises(CanonicalOutboundError) as raised:
        asyncio.run(_service(tmp_path, channel_id, connection_service).deliver(
            message=_message(channel_id), authenticated_instance=None,
        ))

    assert raised.value.status_code == status_code
    assert raised.value.code == "provider_delivery_failed"
    assert "provider detail" not in str(raised.value)


def test_canonical_instagram_preserves_instagram_transport_credential_errors(tmp_path) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService(
        error=InstagramGraphError("credential rejected", status_code=401),
    )

    with pytest.raises(CanonicalOutboundError) as raised:
        asyncio.run(_service(tmp_path, channel_id, connection_service).deliver(
            message=_message(channel_id), authenticated_instance=None,
        ))

    assert raised.value.status_code == 401
    assert raised.value.code == "provider_delivery_failed"


def test_canonical_instagram_reuses_accepted_idempotency_result_without_second_send(tmp_path) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService()
    service = _service(tmp_path, channel_id, connection_service)
    message = _message(channel_id, idempotency_key="core:instagram:dedupe")

    first = asyncio.run(service.deliver(message=message, authenticated_instance=None))
    second = asyncio.run(service.deliver(message=message, authenticated_instance=None))

    assert first["providerMessageId"] == second["providerMessageId"] == "ig-message-1"
    assert len(connection_service.calls) == 1


def test_canonical_instagram_idempotency_scope_keeps_full_opaque_external_id(tmp_path) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService()
    service = _service(tmp_path, channel_id, connection_service)
    shared_prefix = "1784" + "x" * 124
    first = _message(channel_id, recipient=f"{shared_prefix}A", idempotency_key="core:instagram:long-recipient")
    second = _message(channel_id, recipient=f"{shared_prefix}B", idempotency_key="core:instagram:long-recipient")

    asyncio.run(service.deliver(message=first, authenticated_instance=None))
    asyncio.run(service.deliver(message=second, authenticated_instance=None))

    assert [call["external_id"] for call in connection_service.calls] == [
        f"{shared_prefix}A",
        f"{shared_prefix}B",
    ]


def test_canonical_instagram_concurrent_idempotency_claim_allows_one_provider_send(tmp_path) -> None:
    channel_id = str(uuid4())

    class _BlockingConnectionService(_InstagramConnectionService):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_instagram_text(self, **kwargs):
            self.calls.append(kwargs)
            self.started.set()
            await self.release.wait()
            return self.result

    async def run() -> None:
        connection_service = _BlockingConnectionService()
        service = _service(tmp_path, channel_id, connection_service)
        message = _message(channel_id, idempotency_key="core:instagram:concurrent")
        first = asyncio.create_task(service.deliver(message=message, authenticated_instance=None))
        await connection_service.started.wait()
        with pytest.raises(CanonicalOutboundError) as raised:
            await service.deliver(message=message, authenticated_instance=None)
        assert raised.value.status_code == 409
        assert raised.value.code == "idempotency_in_progress"
        connection_service.release.set()
        await first
        assert len(connection_service.calls) == 1

    asyncio.run(run())


def test_canonical_instagram_binding_mismatch_is_rejected(tmp_path) -> None:
    channel_id = str(uuid4())
    connection_service = _InstagramConnectionService()
    service = _service(tmp_path, channel_id, connection_service)

    with pytest.raises(CanonicalOutboundError) as raised:
        asyncio.run(service.deliver(message=_message(str(uuid4())), authenticated_instance="instagram_runtime"))

    assert raised.value.status_code == 409
    assert raised.value.code == "channel_connection_mismatch"
    assert connection_service.calls == []


def test_canonical_unsupported_provider_channel_remains_501(tmp_path) -> None:
    channel_id = str(uuid4())
    message = CanonicalOutboundMessage.model_validate({
        **_message(channel_id).model_dump(by_alias=True),
        "channel": {"id": channel_id, "type": "whatsapp", "provider": "meta"},
    })
    service = _service(
        tmp_path,
        channel_id,
        _InstagramConnectionService(),
        registry=_Registry(channel_id, provider="meta", channel_type="whatsapp"),
    )

    with pytest.raises(CanonicalOutboundError) as raised:
        asyncio.run(service.deliver(message=message, authenticated_instance=None))

    assert raised.value.status_code == 501
    assert raised.value.code == "provider_not_implemented"


def test_canonical_endpoint_returns_202_for_instagram_success(tmp_path, monkeypatch) -> None:
    channel_id = str(uuid4())
    service = _service(tmp_path, channel_id, _InstagramConnectionService())
    monkeypatch.setattr(canonical_outbound, "CanonicalOutboundService", lambda: service)
    app = FastAPI()
    app.include_router(canonical_outbound.router)

    response = TestClient(app).post(
        "/v1/outbound/messages",
        json=_message(channel_id).model_dump(by_alias=True, mode="json"),
        headers={"X-Botly-Contract-Version": "canonical-v1"},
    )

    assert response.status_code == 202
    assert response.json()["providerMessageId"] == "ig-message-1"
