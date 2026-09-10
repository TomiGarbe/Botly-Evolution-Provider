from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.domain.defaults import get_default_domain_registry
from app.domain.models import ChannelId, MethodId, ProvisionedChannel
from app.domain.registries import DomainRegistry, RegistryError
from app.domain.runtime_resolver import RuntimeResolver
from app.platforms.instagram import InstagramGraphClient
from app.providers.base import ProviderCapabilities


@dataclass(frozen=True)
class InstagramSendRequest:
    channel: ProvisionedChannel
    recipient_id: str
    access_token: str
    text: str | None = None
    attachment_type: str | None = None
    attachment_url: str | None = None


class MetaInstagramProvider:
    """Meta transport adapter for Instagram, limited to the G1 foundation."""

    provider_id = "meta"
    channel_type = "instagram"
    channel_id = ChannelId.INSTAGRAM
    method_id = MethodId.OFFICIAL

    _capabilities = ProviderCapabilities(
        inbound_text="foundation",
        inbound_media="foundation",
        outbound_text="foundation",
        webhook="implemented",
        reactions="foundation",
    )

    def __init__(
        self,
        *,
        domain: DomainRegistry | None = None,
        transport: InstagramGraphClient | None = None,
    ) -> None:
        self._domain = domain or get_default_domain_registry()
        self._transport = transport or InstagramGraphClient()
        self._runtime_resolver = RuntimeResolver(self._domain)

    def validate_payload(self, payload: dict[str, Any]) -> bool:
        return payload.get("object") == "instagram" and isinstance(payload.get("entry"), list)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def verify_challenge(
        self,
        *,
        mode: str | None,
        token: str | None,
        challenge: str | None,
        verify_token: str,
    ) -> str | None:
        if mode == "subscribe" and token and hmac.compare_digest(token, verify_token):
            return challenge
        return None

    def verify_signature(self, *, body: bytes, signature: str | None, app_secret: str) -> bool:
        if not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        provided = signature.removeprefix("sha256=")
        return hmac.compare_digest(provided, expected)

    def normalize_webhook(self, payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        if not self.validate_payload(payload):
            return ()
        normalized: list[dict[str, Any]] = []
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            provider_account_id = str(entry.get("id") or "")
            for event in entry.get("messaging") or []:
                if isinstance(event, dict):
                    normalized.append(self._normalize_event(event, provider_account_id=provider_account_id, raw=payload))
        return tuple(normalized)

    async def send(self, request: InstagramSendRequest) -> dict[str, Any]:
        self._assert_channel(request.channel)
        if not request.recipient_id.strip():
            raise ValueError("recipient_id is required")
        if not request.access_token.strip():
            raise ValueError("access_token is required")

        graph_node_id = str(
            request.channel.metadata.get("graphSendNodeId")
            or request.channel.metadata.get("sourcePageId")
            or request.channel.metadata.get("sourceExternalId")
            or ""
        ).strip()
        if not graph_node_id:
            raise ValueError("channel metadata graphSendNodeId or sourceExternalId is required")

        message = self._build_send_message(request)
        response = await self._transport.send_message(
            instagram_account_id=graph_node_id,
            access_token=request.access_token,
            recipient_id=request.recipient_id,
            message=message,
        )
        return response if isinstance(response, dict) else {"ok": True}

    async def send_text(
        self,
        *,
        channel: ProvisionedChannel,
        recipient_id: str,
        text: str,
        access_token: str,
    ) -> dict[str, Any]:
        return await self.send(
            InstagramSendRequest(
                channel=channel,
                recipient_id=recipient_id,
                text=text,
                access_token=access_token,
            )
        )

    def _assert_channel(self, channel: ProvisionedChannel) -> None:
        resolution = self._runtime_resolver.resolve_channel(channel)
        if resolution.integration.channel_id != self.channel_id or resolution.integration.method_id != self.method_id:
            raise RegistryError(f"Channel {channel.id} is not Instagram Official.")

    def _build_send_message(self, request: InstagramSendRequest) -> dict[str, Any]:
        text = str(request.text or "").strip()
        if text:
            return {"text": text}

        attachment_type = str(request.attachment_type or "").strip().lower()
        attachment_url = str(request.attachment_url or "").strip()
        if attachment_type not in {"image", "video", "audio"}:
            raise ValueError("attachment_type must be image, video, or audio")
        if not attachment_url:
            raise ValueError("attachment_url is required")
        return {
            "attachment": {
                "type": attachment_type,
                "payload": {"url": attachment_url},
            }
        }

    def _normalize_event(self, event: dict[str, Any], *, provider_account_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        source_timestamp = _to_int(event.get("timestamp"))
        sender_id = _nested_str(event, "sender", "id")
        recipient_id = _nested_str(event, "recipient", "id") or provider_account_id
        base = {
            "id": str(uuid.uuid4())[:16],
            "event": "INSTAGRAM_MESSAGE",
            "sourceEvent": "instagram.messaging",
            "providerAccountId": provider_account_id,
            "timestamp": now_ms,
            "sourceTimestamp": source_timestamp,
            "layer": "business",
            "sender": sender_id,
            "recipient": recipient_id,
            "fromBot": False,
            "forwarding": {"status": "pending"},
            "error": None,
            "metadata": {
                "channelId": self.channel_id.value,
                "providerId": self.provider_id,
                "providerAccountId": provider_account_id,
                "methodId": self.method_id.value,
                "platformId": "meta",
            },
            "raw": raw,
        }

        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if message:
            return self._normalize_message(base, message=message, sender_id=sender_id, recipient_id=recipient_id)
        if isinstance(event.get("reaction"), dict):
            return self._normalize_reaction(base, reaction=event["reaction"])
        if isinstance(event.get("read"), dict):
            return self._normalize_status_event(base, subtype="read_receipt", event_name="INSTAGRAM_READ_RECEIPT", content="read")
        if isinstance(event.get("delivery"), dict):
            return self._normalize_status_event(base, subtype="delivery", event_name="INSTAGRAM_DELIVERY", content="delivered")
        if isinstance(event.get("typing"), dict):
            return self._normalize_status_event(base, subtype="typing", event_name="INSTAGRAM_TYPING", content="typing")
        return {
            **base,
            "event": "INSTAGRAM_UNKNOWN",
            "type": "message",
            "subtype": "unknown",
            "originalType": "unknown",
            "messageType": "unknown",
            "direction": "inbound",
            "content": {"text": "[Unsupported message type]"},
            "text": None,
            "status": "received",
            "fromMe": False,
            "context": {},
            "message": {
                "id": None,
                "from": sender_id,
                "fromMe": False,
                "messageType": "unknown",
                "kind": "unknown",
                "text": None,
            },
            "media": None,
        }

    def _normalize_message(
        self,
        base: dict[str, Any],
        *,
        message: dict[str, Any],
        sender_id: str | None,
        recipient_id: str | None,
    ) -> dict[str, Any]:
        from_me = bool(message.get("is_echo"))
        text = str(message.get("text")) if message.get("text") is not None else None
        attachment = _first_dict(message.get("attachments"))
        reaction = message.get("reaction") if isinstance(message.get("reaction"), dict) else None
        if reaction:
            return self._normalize_reaction(base, reaction=reaction, message=message)

        kind = "text" if text is not None else _attachment_kind(attachment)
        media = _media_from_attachment(attachment, kind)
        content = {"text": text or ""}
        if media:
            content = {"text": media.get("caption") or "", "media": media}

        return {
            **base,
            "event": "INSTAGRAM_MESSAGE",
            "type": "message",
            "subtype": kind,
            "originalType": kind,
            "messageType": kind,
            "direction": "outbound" if from_me else "inbound",
            "content": content,
            "text": text,
            "status": "received",
            "fromMe": from_me,
            "context": {
                "mid": message.get("mid"),
                "replyTo": _nested_str(message, "reply_to", "mid"),
            },
            "message": {
                "id": message.get("mid"),
                "from": recipient_id if from_me else sender_id,
                "fromMe": from_me,
                "messageType": kind,
                "kind": kind,
                "text": text,
            },
            "media": media,
        }

    def _normalize_reaction(
        self,
        base: dict[str, Any],
        *,
        reaction: dict[str, Any],
        message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = str(reaction.get("mid") or reaction.get("message_id") or (message or {}).get("mid") or "") or None
        emoji = reaction.get("emoji") or reaction.get("reaction")
        return {
            **base,
            "event": "INSTAGRAM_REACTION",
            "type": "message",
            "subtype": "reaction",
            "originalType": "reaction",
            "messageType": "reaction",
            "direction": "inbound",
            "content": {"emoji": emoji},
            "text": emoji,
            "status": "received",
            "fromMe": False,
            "context": {"targetMessage": {"id": target_id} if target_id else None},
            "message": {
                "id": (message or {}).get("mid"),
                "from": base.get("sender"),
                "fromMe": False,
                "messageType": "reaction",
                "kind": "reaction",
                "text": emoji,
            },
            "media": None,
        }

    def _normalize_status_event(
        self,
        base: dict[str, Any],
        *,
        subtype: str,
        event_name: str,
        content: str,
    ) -> dict[str, Any]:
        return {
            **base,
            "event": event_name,
            "type": "event",
            "subtype": subtype,
            "originalType": subtype,
            "messageType": subtype,
            "direction": "system",
            "content": {"text": content},
            "text": content,
            "status": content,
            "fromMe": False,
            "context": {},
            "message": {
                "id": None,
                "from": base.get("sender"),
                "fromMe": False,
                "messageType": subtype,
                "kind": subtype,
                "text": content,
            },
            "media": None,
        }


def _nested_str(value: dict[str, Any], *path: str) -> str | None:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if current is None:
        return None
    text = str(current).strip()
    return text or None


def _first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return None


def _attachment_kind(attachment: dict[str, Any] | None) -> str:
    raw = str((attachment or {}).get("type") or "unknown").lower()
    if raw == "file":
        return "document"
    if raw in {"image", "video", "audio"}:
        return raw
    return "unknown"


def _media_from_attachment(attachment: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
    if not attachment:
        return None
    payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else {}
    return {
        "id": str(payload.get("id") or payload.get("attachment_id") or "") or None,
        "kind": kind,
        "mimeType": payload.get("mime_type"),
        "fileName": payload.get("name"),
        "fileSize": _to_int(payload.get("size")),
        "caption": payload.get("caption"),
        "url": payload.get("url"),
        "downloadSource": "provider-url" if payload.get("url") else "provider-id",
    }


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Kept as an import-compatible name for callers that existed before G1.
InstagramProvider = MetaInstagramProvider
