from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx

from app.domain import ChannelId, ChannelStatus, MethodId, ProvisionedChannel, RuntimeId
from app.platforms.instagram import InstagramGraphClient
from app.providers.instagram import InstagramProvider, MetaInstagramProvider


def _instagram_settings():
    return SimpleNamespace(
        instagram_graph_api_url="https://graph.instagram.com",
        instagram_graph_api_version="v23.0",
        meta_signup_timeout_seconds=30,
    )


def _instagram_channel() -> ProvisionedChannel:
    return ProvisionedChannel(
        id="instagram:official:abc123",
        channel_id=ChannelId.INSTAGRAM,
        method_id=MethodId.OFFICIAL,
        integration_id="instagram.official.meta",
        runtime_id=RuntimeId.META,
        display_name="acme",
        status=ChannelStatus.ACTIVE,
        metadata={
            "sourceResourceId": "meta:INSTAGRAM:ig_1",
            "sourceResourceType": "INSTAGRAM",
            "sourceExternalId": "ig_1",
            "sourcePageId": "page_1",
            "graphSendNodeId": "page_1",
        },
    )


def test_instagram_provider_validates_challenge_and_signature() -> None:
    provider = InstagramProvider()
    body = b'{"object":"instagram"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert provider.verify_challenge(
        mode="subscribe",
        token="verify-token",
        challenge="challenge-value",
        verify_token="verify-token",
    ) == "challenge-value"
    assert provider.verify_signature(body=body, signature=f"sha256={digest}", app_secret="secret")
    assert not provider.verify_signature(body=body, signature="sha256=bad", app_secret="secret")


def test_instagram_provider_normalizes_text_media_reaction_and_typing_events() -> None:
    provider = InstagramProvider()
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "ig_1",
                "messaging": [
                    {
                        "sender": {"id": "user_1"},
                        "recipient": {"id": "ig_1"},
                        "timestamp": 1710000000000,
                        "message": {"mid": "mid_text", "text": "hola"},
                    },
                    {
                        "sender": {"id": "user_1"},
                        "recipient": {"id": "ig_1"},
                        "timestamp": 1710000000001,
                        "message": {
                            "mid": "mid_image",
                            "attachments": [
                                {"type": "image", "payload": {"url": "https://cdn.example/image.jpg"}}
                            ],
                        },
                    },
                    {
                        "sender": {"id": "user_1"},
                        "recipient": {"id": "ig_1"},
                        "timestamp": 1710000000002,
                        "reaction": {"mid": "mid_text", "emoji": "\u2764\ufe0f"},
                    },
                    {
                        "sender": {"id": "user_1"},
                        "recipient": {"id": "ig_1"},
                        "timestamp": 1710000000003,
                        "typing": {"status": "on"},
                    },
                ],
            }
        ],
    }

    normalized = provider.normalize_webhook(payload)

    assert provider.validate_payload(payload)
    assert [event["messageType"] for event in normalized] == ["text", "image", "reaction", "typing"]
    assert normalized[0]["content"] == {"text": "hola"}
    assert normalized[1]["media"]["kind"] == "image"
    assert normalized[2]["content"] == {"emoji": "\u2764\ufe0f"}
    assert normalized[3]["type"] == "event"
    assert all(event["metadata"]["channelId"] == "instagram" for event in normalized)
    assert all(event["metadata"]["providerId"] == "meta" for event in normalized)
    assert normalized[0]["providerAccountId"] == "ig_1"
    assert "instance" not in normalized[0]


def test_instagram_provider_capabilities_are_foundation_not_ready() -> None:
    capabilities = MetaInstagramProvider().capabilities.public_dict()

    assert capabilities["inboundText"] == "foundation"
    assert capabilities["outboundText"] == "foundation"
    assert capabilities["webhook"] == "implemented"
    assert capabilities["businessLogin"] == "not_implemented"
    assert capabilities["enabled"] is False
    assert capabilities["ready"] is False


def test_instagram_provider_sends_text_through_instagram_graph_with_bearer_auth() -> None:
    requests: list[httpx.Request] = []

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"recipient_id": "user_1", "message_id": "mid_1"})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://graph.instagram.com",
        )
        provider = InstagramProvider(transport=InstagramGraphClient(client=client, settings_factory=_instagram_settings))
        response = await provider.send_text(
            channel=_instagram_channel(),
            recipient_id="user_1",
            text="hola",
            access_token="page-token",
        )
        await client.aclose()

        assert response == {"recipient_id": "user_1", "message_id": "mid_1"}

    asyncio.run(run())

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.host == "graph.instagram.com"
    assert requests[0].url.path == "/v23.0/page_1/messages"
    assert requests[0].headers["Authorization"] == "Bearer page-token"
    assert "access_token" not in requests[0].url.params
    assert "graph.facebook.com" not in str(requests[0].url)
    assert json.loads(requests[0].content) == {
        "recipient": {"id": "user_1"},
        "message": {"text": "hola"},
    }
