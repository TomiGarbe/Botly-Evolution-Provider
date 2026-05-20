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

STATUS_ALIASES = {
    "pending": "sent",
    "server_ack": "sent",
    "sent": "sent",
    "delivery_ack": "delivered",
    "delivered": "delivered",
    "read": "read",
    "read_ack": "read",
    "played": "played",
    "playedback": "played",
}


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


def _normalize_status(raw_status: Any) -> str | None:
    value = str(raw_status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not value:
        return None
    return STATUS_ALIASES.get(value)


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_message_update(payload: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    data_items = _as_dict_items(payload.get("data"))
    if not data_items:
        data_items = [_ensure_dict(payload.get("data"))]

    chosen: dict[str, Any] | None = None
    chosen_status: str | None = None
    for item in data_items:
        candidate = _normalize_status(
            _first(item, ("status",))
            or _first(item, ("message", "status"))
            or _first(item, ("update", "status"))
            or _first(item, ("messageUpdate", "status"))
        )
        if candidate:
            chosen = item
            chosen_status = candidate

    if not chosen:
        chosen = data_items[-1] if data_items else {}

    key = _ensure_dict(chosen.get("key"))
    message_id = str(key.get("id") or _first(chosen, ("message", "key", "id")) or "")
    remote_jid = str(key.get("remoteJid") or _first(chosen, ("message", "key", "remoteJid")) or "")
    from_me = bool(key.get("fromMe"))

    return {
        **base,
        "layer": "business",
        "direction": "system",
        "type": "event",
        "subtype": "message_status",
        "originalType": "messages.update",
        "content": {"text": chosen_status or "[Unknown message status update]"},
        "media": None,
        "metadata": {
            "status": chosen_status or "unknown",
            "messageId": message_id or None,
            "updatesCount": len(data_items),
            "statusFound": bool(chosen_status),
            "rawStatus": _first(chosen, ("status",))
            or _first(chosen, ("message", "status"))
            or _first(chosen, ("update", "status"))
            or _first(chosen, ("messageUpdate", "status")),
        },
        "context": {
            "instance": payload.get("instance"),
            "remoteJid": remote_jid or None,
            "fromMe": from_me,
        },
        "status": chosen_status or "unknown",
        "messageId": message_id or None,
        "fromMe": from_me,
        "sender": payload.get("instance") if from_me else (remote_jid or payload.get("instance")),
        "recipient": remote_jid or payload.get("instance"),
        "messageType": "delivery",
        "text": chosen_status or "unknown",
        "forwarding": {"status": "n/a"},
        "error": None,
        "message": {
            "id": message_id or None,
            "from": remote_jid or None,
            "fromMe": from_me,
            "kind": "delivery",
            "text": chosen_status or "unknown",
            "messageType": "messages.update",
        },
        "raw": payload,
    }


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


def _extract_text(message: dict[str, Any]) -> str | None:
    text = _first(
        message,
        ("conversation",),
        ("extendedTextMessage", "text"),
        ("imageMessage", "caption"),
        ("videoMessage", "caption"),
    )
    return str(text) if text is not None else None


def _phone_from_jid(jid: str | None) -> str | None:
    value = str(jid or "").strip()
    if not value:
        return None
    return value.split("@", 1)[0] or None


def _extract_context_info(message: dict[str, Any], message_type: str) -> dict[str, Any]:
    mt = str(message_type or "")
    if mt == "extendedTextMessage":
        return _ensure_dict(_first(message, ("extendedTextMessage", "contextInfo")))
    return _ensure_dict(_first(message, (mt, "contextInfo")))


def _extract_quoted_summary(context_info: dict[str, Any]) -> dict[str, Any] | None:
    stanza_id = str(context_info.get("stanzaId") or "").strip()
    participant = str(context_info.get("participant") or "").strip()
    remote_jid = str(context_info.get("remoteJid") or "").strip()
    quoted_message = _ensure_dict(context_info.get("quotedMessage"))
    quoted_type = str(next(iter(quoted_message.keys()), "unknown"))
    quoted_text = _extract_text(quoted_message)

    if not stanza_id and not participant and not remote_jid and not quoted_message:
        return None

    media_type = None
    if quoted_type == "audioMessage":
        media_type = "voice_note" if bool(_first(quoted_message, ("audioMessage", "ptt"))) else "audio"
    elif quoted_type in {"imageMessage", "videoMessage", "documentMessage", "stickerMessage"}:
        media_type = quoted_type.replace("Message", "").lower()

    preview = quoted_text
    if not preview and media_type:
        preview = f"[{media_type}]"

    return {
        "messageId": stanza_id or None,
        "sender": participant or None,
        "chatJid": remote_jid or None,
        "type": quoted_type,
        "text": quoted_text,
        "mediaType": media_type,
        "preview": preview,
        "raw": quoted_message or None,
    }


def _extract_mentions(context_info: dict[str, Any]) -> list[dict[str, Any]]:
    raw_mentions = context_info.get("mentionedJid")
    if not isinstance(raw_mentions, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw_mentions:
        jid = str(item or "").strip()
        if not jid:
            continue
        items.append({"jid": jid, "phone": _phone_from_jid(jid)})
    return items


def _build_chat_context(data: dict[str, Any], instance: Any) -> dict[str, Any]:
    remote_jid = str(_first(data, ("key", "remoteJid")) or "")
    participant = str(_first(data, ("key", "participant")) or "")
    is_group = remote_jid.endswith("@g.us")
    sender = participant if is_group and participant else remote_jid
    return {
        "jid": remote_jid or None,
        "isGroup": is_group,
        "groupId": remote_jid if is_group else None,
        "participant": participant or None,
        "sender": sender or None,
        "instance": instance,
    }


def _build_context_and_metadata(data: dict[str, Any], message: dict[str, Any], message_type: str, instance: Any, from_me: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    context_info = _extract_context_info(message, message_type)
    quoted = _extract_quoted_summary(context_info)
    mentions = _extract_mentions(context_info)
    chat = _build_chat_context(data, instance)
    is_reply = quoted is not None
    is_group_reply = bool(is_reply and chat.get("isGroup"))
    is_self_reply = bool(is_reply and from_me)

    forwarding_score = context_info.get("forwardingScore")
    score_value = int(forwarding_score) if str(forwarding_score or "").isdigit() else 0
    has_business_forward = isinstance(context_info.get("businessForwardInfo"), dict)
    is_forwarded = bool(context_info.get("isForwarded")) or score_value > 0 or has_business_forward

    context = {
        "quoted": quoted,
        "mentions": mentions,
        "chat": chat,
    }
    metadata = {
        "isReply": is_reply,
        "isSelfReply": is_self_reply,
        "isGroupReply": is_group_reply,
        "replyKind": "self_reply" if is_self_reply else ("group_reply" if is_group_reply else ("reply" if is_reply else "none")),
        "hasMentions": bool(mentions),
        "mentionCount": len(mentions),
        "forwarded": is_forwarded,
        "forwardingScore": score_value if is_forwarded else 0,
        "businessForwardInfo": context_info.get("businessForwardInfo") if has_business_forward else None,
        "messageTimestamp": data.get("messageTimestamp"),
    }
    return context, metadata


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
        return _normalize_message_update(payload, base)

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
    is_unknown_fallback = kind not in BUSINESS_MESSAGE_TYPES

    text = _extract_text(message)

    from_me = bool(_first(data, ("key", "fromMe")))
    remote_jid = _first(data, ("key", "remoteJid"))
    normalized_subtype = kind if not is_unknown_fallback else "unknown"
    context, metadata = _build_context_and_metadata(data, message, message_type, payload.get("instance"), from_me)
    normalized_content: Any = {"text": text} if text is not None else {"text": ""}
    if is_unknown_fallback:
        normalized_content = {"text": "[Unsupported message type]"}

    normalized = {
        **base,
        "layer": "business",
        "direction": "inbound" if not from_me else "outbound",
        "type": "message",
        "subtype": normalized_subtype,
        "originalType": message_type,
        "messageType": normalized_subtype,
        "sender": payload.get("instance") if from_me else remote_jid,
        "recipient": remote_jid if from_me else payload.get("instance"),
        "content": normalized_content,
        "text": text,
        "status": "received",
        "fromMe": from_me,
        "fromBot": False,
        "forwarding": {"status": "pending"},
        "error": None,
        "metadata": {**metadata, "unknownTypeDetected": is_unknown_fallback},
        "context": context,
        "message": {
            "id": _first(data, ("key", "id")),
            "from": remote_jid,
            "fromMe": from_me,
            "participant": _first(data, ("key", "participant")),
            "pushName": data.get("pushName"),
            "messageType": message_type,
            "kind": normalized_subtype,
            "text": text,
            "messageTimestamp": data.get("messageTimestamp"),
        },
        "media": _extract_media(message, kind if not is_unknown_fallback else "unknown"),
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
