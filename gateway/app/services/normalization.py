from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

from app.core.config import get_settings

MEDIA_MESSAGE_KEYS = (
    "imageMessage",
    "audioMessage",
    "videoMessage",
    "documentMessage",
    "stickerMessage",
)

IGNORED_MESSAGE_TYPES = {
    "protocolmessage",
    "senderkeydistributionmessage",
    "messagecontextinfo",
    "encmessage",
    "senderkeymessage",
}

BUSINESS_MESSAGE_TYPES = {
    "text",
    "audio",
    "image",
    "video",
    "document",
    "sticker",
    "voice_note",
}

TECHNICAL_EVENTS = {
    "PRESENCE_UPDATE",
    "PRESENCE",
    "CALL",
    "CHATS_UPDATE",
    "CONTACTS_UPDATE",
    "CONNECTION_UPDATE",
}

_settings = get_settings()
_raw_events: deque[dict[str, Any]] = deque(maxlen=_settings.webhook_event_retention)
_operational_events: deque[dict[str, Any]] = deque(maxlen=_settings.webhook_event_retention)
_business_events: deque[dict[str, Any]] = deque(maxlen=_settings.webhook_event_retention)
_media_index: dict[str, dict[str, Any]] = {}


def _first(value: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        cur = value
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok and cur is not None:
            return cur
    return None


def _canonical_event_name(raw_event: Any) -> str:
    event = str(raw_event or "UNKNOWN").strip()
    token = event.lower().replace("_", ".").replace("-", ".")
    token = ".".join(part for part in token.split(".") if part)
    aliases = {
        "messages.upsert": "MESSAGES_UPSERT",
        "messages.update": "MESSAGES_UPDATE",
        "messages.delete": "MESSAGES_DELETE",
        "send.message": "SEND_MESSAGE",
        "connection.update": "CONNECTION_UPDATE",
        "presence.update": "PRESENCE_UPDATE",
        "presence": "PRESENCE",
        "call": "CALL",
        "chats.update": "CHATS_UPDATE",
        "contacts.update": "CONTACTS_UPDATE",
    }
    return aliases.get(token, event.upper())


def _guess_kind(message: dict[str, Any], message_type: str) -> str:
    if message_type in ("conversation", "extendedTextMessage"):
        return "text"
    if "stickerMessage" in message:
        return "sticker"
    if "audioMessage" in message:
        voice = bool(_first(message, ("audioMessage", "ptt")))
        return "voice_note" if voice else "audio"
    if "imageMessage" in message:
        return "image"
    if "videoMessage" in message:
        return "video"
    if "documentMessage" in message:
        return "document"
    return "unknown"


def _extract_media(message: dict[str, Any], kind: str) -> dict[str, Any] | None:
    media_key_name = next((k for k in MEDIA_MESSAGE_KEYS if k in message), None)
    if not media_key_name:
        return None
    raw = message.get(media_key_name) or {}
    direct_path = str(raw.get("directPath") or "").strip()
    url = str(raw.get("url") or "").strip()
    media_id = str(raw.get("mediaKey") or raw.get("fileSha256") or uuid.uuid4())
    dimensions = {
        "width": raw.get("width"),
        "height": raw.get("height"),
    }
    if dimensions["width"] is None and dimensions["height"] is None:
        dimensions = None
    return {
        "id": media_id,
        "kind": kind,
        "mimeType": raw.get("mimetype"),
        "fileName": raw.get("fileName"),
        "fileSize": raw.get("fileLength"),
        "mediaKey": raw.get("mediaKey"),
        "duration": raw.get("seconds"),
        "caption": raw.get("caption"),
        "url": url,
        "directPath": direct_path,
        "thumbnail": raw.get("jpegThumbnail"),
        "dimensions": dimensions,
        "isVoiceNote": bool(raw.get("ptt")),
    }


def normalize_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    source_event = str(payload.get("event", "UNKNOWN"))
    event = _canonical_event_name(source_event)
    now_ms = int(time.time() * 1000)

    base = {
        "id": str(uuid.uuid4())[:16],
        "event": event,
        "sourceEvent": source_event,
        "instance": payload.get("instance"),
        "timestamp": now_ms,
    }

    if event in TECHNICAL_EVENTS:
        return {**base, "layer": "technical", "reason": "technical_event"}

    if event == "MESSAGES_UPDATE":
        status_value = str(
            _first(payload, ("data", "status"))
            or _first(payload, ("data", "message", "status"))
            or _first(payload, ("data", "update", "status"))
            or ""
        ).strip().lower()
        if not status_value:
            return {**base, "layer": "technical", "reason": "status_missing"}

        sender = str(_first(payload, ("data", "key", "remoteJid")) or "")
        from_me = bool(_first(payload, ("data", "key", "fromMe")))
        return {
            **base,
            "layer": "business",
            "direction": "system",
            "type": "delivery",
            "messageType": "delivery",
            "sender": payload.get("instance") if from_me else sender,
            "recipient": sender if from_me else payload.get("instance"),
            "content": status_value,
            "text": status_value,
            "status": status_value,
            "fromMe": from_me,
            "fromBot": False,
            "forwarding": {"status": "n/a"},
            "error": None,
            "message": {
                "id": _first(payload, ("data", "key", "id")),
                "from": _first(payload, ("data", "key", "remoteJid")),
                "fromMe": from_me,
                "kind": "delivery",
                "text": status_value,
            },
            "media": None,
            "raw": payload,
        }

    # Evolution/Baileys emite mensajes reales principalmente via messages.upsert.
    # Nunca debe clasificarse como técnico porque es el evento de negocio base del chat.
    if event not in {"MESSAGES_UPSERT", "SEND_MESSAGE"}:
        return {**base, "layer": "technical", "reason": "non_business_event"}

    data = payload.get("data") or {}
    message = data.get("message") or {}
    message_type = str(data.get("messageType") or next(iter(message.keys()), "unknown"))
    message_type_lower = message_type.lower()

    if message_type_lower in IGNORED_MESSAGE_TYPES:
        return {**base, "layer": "technical", "reason": f"ignored_message_type:{message_type_lower}"}

    kind = _guess_kind(message, message_type)
    if kind not in BUSINESS_MESSAGE_TYPES:
        return {**base, "layer": "technical", "reason": f"unknown_kind:{kind}"}

    text = _first(
        message,
        ("conversation",),
        ("extendedTextMessage", "text"),
        ("imageMessage", "caption"),
        ("videoMessage", "caption"),
    )

    from_me = bool(_first(data, ("key", "fromMe")))
    remote_jid = _first(data, ("key", "remoteJid"))
    normalized = {
        **base,
        "layer": "business",
        "direction": "inbound" if not from_me else "outbound",
        "type": "message",
        "messageType": kind,
        "sender": payload.get("instance") if from_me else remote_jid,
        "recipient": remote_jid if from_me else payload.get("instance"),
        "content": text,
        "text": text,
        "status": "received",
        "fromMe": from_me,
        "fromBot": False,
        "forwarding": {"status": "pending"},
        "error": None,
        "message": {
            "id": _first(data, ("key", "id")),
            "from": remote_jid,
            "fromMe": from_me,
            "participant": _first(data, ("key", "participant")),
            "pushName": data.get("pushName"),
            "messageType": message_type,
            "kind": kind,
            "text": text,
            "messageTimestamp": data.get("messageTimestamp"),
        },
        "media": _extract_media(message, kind),
        "raw": payload,
    }
    return normalized


def save_business_event(event: dict[str, Any]) -> None:
    if event.get("layer") != "business":
        return
    _business_events.appendleft(event)


def save_raw_event(event: dict[str, Any]) -> None:
    if not _settings.debug:
        return
    _raw_events.appendleft(event)


def save_event(normalized: dict[str, Any]) -> None:
    if normalized.get("layer") != "business":
        return
    _business_events.appendleft(normalized)
    media = normalized.get("media")
    message = normalized.get("message") or {}
    if isinstance(media, dict):
        _media_index[str(media.get("id"))] = {
            **media,
            "instance": normalized.get("instance"),
            "messageId": message.get("id"),
            "savedAt": int(time.time()),
        }


def save_pipeline_event(
    *,
    stage: str,
    status: str,
    instance: str | None = None,
    message_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
    event: str = "PIPELINE",
    details: dict[str, Any] | None = None,
) -> None:
    _operational_events.appendleft(
        {
            "id": str(uuid.uuid4())[:16],
            "layer": "operational",
            "event": event,
            "instance": instance,
            "timestamp": int(time.time() * 1000),
            "pipeline": {
                "stage": stage,
                "status": status,
                "requestId": request_id,
                "conversationId": conversation_id,
                "messageId": message_id,
            },
            "details": details or {},
        }
    )


def list_events(instance: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    merged = list(_business_events) + list(_operational_events)
    merged.sort(key=lambda item: int(item.get("timestamp") or 0), reverse=True)

    items: list[dict[str, Any]] = []
    for event in merged:
        if instance and event.get("instance") != instance:
            continue
        items.append(event)
        if len(items) >= limit:
            break
    return items


def get_media(media_id: str) -> dict[str, Any] | None:
    return _media_index.get(media_id)
