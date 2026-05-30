from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

MEDIA_MESSAGE_KEYS = (
    "imageMessage",
    "audioMessage",
    "videoMessage",
    "documentMessage",
    "stickerMessage",
)

IGNORED_MESSAGE_TYPES = {
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
    "interactive_reply",
    "interactive_unknown",
    "interactive_message",
    "reaction",
    "contact",
    "location",
    "poll_create",
    "poll_update",
    "special",
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
logger = get_logger(__name__)
_raw_events: deque[dict[str, Any]] = deque(maxlen=_settings.webhook_event_retention)
_operational_events: deque[dict[str, Any]] = deque(maxlen=_settings.webhook_event_retention)
_business_events: deque[dict[str, Any]] = deque(maxlen=_settings.webhook_event_retention)
_media_index: dict[str, dict[str, Any]] = {}
_business_event_keys_order: deque[str] = deque()
_business_event_keys: set[str] = set()
_business_event_keys_max = max(1000, _settings.webhook_event_retention * 3)

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
    if message_type in {
        "buttonsResponseMessage",
        "listResponseMessage",
        "templateButtonReplyMessage",
        "interactiveResponseMessage",
        "nativeFlowResponseMessage",
    }:
        return "interactive_reply"
    if message_type in {"interactiveMessage", "templateMessage", "hydratedTemplate"}:
        return "interactive_message"
    if message_type == "reactionMessage":
        return "reaction"
    if message_type in {"contactMessage", "contactsArrayMessage"}:
        return "contact"
    if message_type in {"locationMessage", "liveLocationMessage"}:
        return "location"
    if message_type == "pollCreationMessage":
        return "poll_create"
    if message_type == "pollUpdateMessage":
        return "poll_update"
    if message_type in {
        "eventMessage",
        "protocolMessage",
        "editedMessage",
        "ephemeralMessage",
        "viewOnceMessage",
        "viewOnceMessageV2",
        "viewOnceMessageV2Extension",
        "keepInChatMessage",
    }:
        return "special"
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
    media_id = str(uuid.uuid4())
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
        "fileSha256": raw.get("fileSha256"),
        "fileEncSha256": raw.get("fileEncSha256"),
        "duration": raw.get("seconds"),
        "caption": raw.get("caption"),
        "url": url,
        "directPath": direct_path,
        "thumbnail": raw.get("jpegThumbnail"),
        "dimensions": dimensions,
        "isVoiceNote": bool(raw.get("ptt")),
        "downloadSource": "provider-url",
    }


def _extract_text(message: dict[str, Any]) -> str | None:
    text = _first(
        message,
        ("conversation",),
        ("extendedTextMessage", "text"),
        ("imageMessage", "caption"),
        ("videoMessage", "caption"),
        ("buttonsResponseMessage", "selectedDisplayText"),
        ("templateButtonReplyMessage", "selectedDisplayText"),
        ("listResponseMessage", "title"),
        ("interactiveResponseMessage", "body", "text"),
        ("reactionMessage", "text"),
        ("locationMessage", "name"),
        ("locationMessage", "address"),
        ("pollCreationMessage", "name"),
    )
    return str(text) if text is not None else None


def _extract_contacts(message: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    contact = _ensure_dict(message.get("contactMessage"))
    if contact:
        out.append(contact)
    arr = _ensure_dict(message.get("contactsArrayMessage"))
    for item in arr.get("contacts") or []:
        if isinstance(item, dict):
            out.append(item)

    parsed: list[dict[str, Any]] = []
    for node in out:
        name = str(node.get("displayName") or node.get("name") or "").strip() or None
        vcard = str(node.get("vcard") or "").strip() or None
        phone = None
        org = None
        if vcard:
            for raw_line in vcard.splitlines():
                line = raw_line.strip()
                up = line.upper()
                if phone is None and "TEL" in up and ":" in line:
                    phone = line.split(":", 1)[1].strip() or None
                if org is None and up.startswith("ORG:"):
                    org = line.split(":", 1)[1].strip() or None
        parsed.append(
            {
                "name": name,
                "phone": phone,
                "vcard": vcard,
                "organization": org,
                "raw": node,
            }
        )
    return parsed


def _extract_location(message: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    node = _ensure_dict(message.get("locationMessage"))
    live = _ensure_dict(message.get("liveLocationMessage"))
    src = live if live else node
    if not src:
        return None, {}
    content = {
        "latitude": src.get("degreesLatitude") or src.get("latitude"),
        "longitude": src.get("degreesLongitude") or src.get("longitude"),
        "name": src.get("name"),
        "address": src.get("address"),
        "url": src.get("url"),
    }
    clean = {k: v for k, v in content.items() if v is not None}
    meta = {
        "liveLocation": bool(live),
        "expiration": src.get("timeOffset") or src.get("expiration"),
        "accuracy": src.get("accuracyInMeters") or src.get("accuracy"),
    }
    return clean or None, {k: v for k, v in meta.items() if v is not None}


def _extract_reaction(message: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    node = _ensure_dict(message.get("reactionMessage"))
    if not node:
        return None, None, {}
    emoji = str(node.get("text") or "").strip()
    key = _ensure_dict(node.get("key"))
    target = {
        "messageId": key.get("id"),
        "sender": key.get("participant") or key.get("remoteJid"),
        "chatJid": key.get("remoteJid"),
    }
    remove_reaction = emoji == ""
    content = {"emoji": emoji or None}
    return content, {k: v for k, v in target.items() if v is not None}, {"removeReaction": remove_reaction}


def _extract_poll(message: dict[str, Any], message_type: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    if message_type == "pollCreationMessage":
        node = _ensure_dict(message.get("pollCreationMessage"))
        options = []
        for item in node.get("options") or []:
            if isinstance(item, dict):
                name = str(item.get("optionName") or item.get("name") or "").strip()
                if name:
                    options.append(name)
        return {
            "title": node.get("name"),
            "options": options,
        }, None, {"pollOptionCount": len(options)}
    if message_type == "pollUpdateMessage":
        node = _ensure_dict(message.get("pollUpdateMessage"))
        selected: list[str] = []
        for item in node.get("selectedOptions") or []:
            if isinstance(item, str) and item.strip():
                selected.append(item.strip())
        poll_id = (
            _first(node, ("pollCreationMessageKey", "id"))
            or _first(node, ("pollCreationMessageKey", "messageId"))
            or node.get("pollCreationMessageKey")
        )
        return {"selectedOptions": selected}, {"targetPollId": poll_id}, {"pollOptionCount": len(selected)}
    return None, None, {}


def _extract_special_flags(message: dict[str, Any], message_type: str, data: dict[str, Any], context_info: dict[str, Any]) -> dict[str, Any]:
    token = message_type.lower()
    protocol = _ensure_dict(message.get("protocolMessage"))
    edited = bool(message.get("editedMessage")) or bool(protocol.get("editedMessage"))
    revoked = bool(protocol) and str(protocol.get("type") or "").upper() in {"REVOKE", "0"}
    ephemeral = bool(message.get("ephemeralMessage")) or bool(context_info.get("expiration")) or bool(data.get("ephemeralStartTimestamp"))
    disappearing_mode = context_info.get("disappearingMode") or data.get("disappearingMode")
    from_business = bool(context_info.get("externalAdReply")) or bool(context_info.get("businessMessageForwardInfo"))
    newsletter = bool(context_info.get("forwardedNewsletterMessageInfo")) or "newsletter" in token
    status_message = str(_first(data, ("key", "remoteJid")) or "").endswith("@status")
    return {
        "ephemeral": ephemeral,
        "edited": edited,
        "revoked": revoked,
        "fromBusiness": from_business,
        "newsletter": newsletter,
        "statusMessage": status_message,
        "disappearingMode": disappearing_mode,
    }


def _phone_from_jid(jid: str | None) -> str | None:
    value = str(jid or "").strip()
    if not value:
        return None
    return value.split("@", 1)[0] or None


def _extract_context_info(message: dict[str, Any], message_type: str) -> dict[str, Any]:
    mt = str(message_type or "")
    if mt == "extendedTextMessage":
        return _ensure_dict(_first(message, ("extendedTextMessage", "contextInfo")))
    if mt == "templateMessage":
        return _ensure_dict(
            _first(message, ("templateMessage", "hydratedTemplate", "contextInfo"))
            or _first(message, ("templateMessage", "contextInfo"))
        )
    if mt == "interactiveMessage":
        return _ensure_dict(_first(message, ("interactiveMessage", "contextInfo")))
    return _ensure_dict(_first(message, (mt, "contextInfo")))


def _to_json_object(text: str | None) -> Any:
    if not text:
        return None
    try:
        import json

        return json.loads(text)
    except Exception:
        return None


def _extract_interaction(message: dict[str, Any], message_type: str) -> tuple[dict[str, Any] | None, str | None]:
    mt = str(message_type or "")
    raw_selection: dict[str, Any] = {}
    interaction_type = "unknown"
    selected_id: str | None = None
    title: str | None = None
    description: str | None = None
    payload: dict[str, Any] = {}

    if mt == "buttonsResponseMessage":
        node = _ensure_dict(message.get("buttonsResponseMessage"))
        interaction_type = "button"
        selected_id = str(node.get("selectedButtonId") or node.get("buttonId") or node.get("selectedId") or "").strip() or None
        title = str(node.get("selectedDisplayText") or node.get("displayText") or "").strip() or None
        raw_selection = node
    elif mt == "templateButtonReplyMessage":
        node = _ensure_dict(message.get("templateButtonReplyMessage"))
        interaction_type = "button"
        selected_id = str(node.get("selectedId") or node.get("buttonId") or "").strip() or None
        title = str(node.get("selectedDisplayText") or node.get("displayText") or "").strip() or None
        payload = {"selectedIndex": node.get("selectedIndex"), "hydratedButtonId": node.get("hydratedButtonId")}
        raw_selection = node
    elif mt == "listResponseMessage":
        node = _ensure_dict(message.get("listResponseMessage"))
        single = _ensure_dict(node.get("singleSelectReply"))
        interaction_type = "list"
        selected_id = str(single.get("selectedRowId") or node.get("rowId") or "").strip() or None
        title = str(single.get("title") or node.get("title") or "").strip() or None
        description = str(single.get("description") or node.get("description") or "").strip() or None
        payload = {
            "section": single.get("sectionTitle") or node.get("sectionTitle"),
            "listType": node.get("listType"),
        }
        raw_selection = {"listResponseMessage": node, "singleSelectReply": single}
    elif mt in {"interactiveResponseMessage", "nativeFlowResponseMessage"}:
        node = _ensure_dict(message.get("interactiveResponseMessage")) if mt == "interactiveResponseMessage" else _ensure_dict(message.get("nativeFlowResponseMessage"))
        native = _ensure_dict(node.get("nativeFlowResponseMessage")) if mt == "interactiveResponseMessage" else node
        interaction_type = "flow"
        selected_id = str(native.get("name") or "").strip() or None
        title = str(_first(node, ("body", "text")) or "").strip() or None
        params_json = native.get("paramsJson")
        payload = {
            "name": native.get("name"),
            "version": native.get("version"),
            "paramsJson": params_json,
            "params": _to_json_object(str(params_json)) if params_json is not None else None,
        }
        raw_selection = {"interactiveResponseMessage": node, "nativeFlowResponseMessage": native}
    elif mt in {"interactiveMessage", "templateMessage", "hydratedTemplate"}:
        node = _ensure_dict(message.get(mt)) if mt in message else _ensure_dict(message)
        interaction_type = "unknown"
        raw_selection = node
    else:
        token = mt.lower()
        if "interactive" in token or "template" in token or "list" in token or "button" in token:
            return {
                "interactionType": "unknown",
                "id": None,
                "title": None,
                "description": None,
                "payload": {},
                "rawSelection": _ensure_dict(message.get(mt)) or message,
            }, "interactive_unknown"
        return None, None

    base = {
        "interactionType": interaction_type,
        "id": selected_id,
        "title": title,
        "description": description,
        "payload": {k: v for k, v in payload.items() if v is not None},
        "rawSelection": raw_selection,
    }
    has_semantic_selection = bool(selected_id or title or description)
    subtype = "interactive_reply" if interaction_type in {"button", "list", "template", "flow"} and has_semantic_selection else "interactive_unknown"
    if mt in {"interactiveMessage", "templateMessage", "hydratedTemplate"}:
        subtype = "interactive_message"
    return base, subtype


def _extract_interactive_origin(context_info: dict[str, Any]) -> dict[str, Any] | None:
    quoted_message = _ensure_dict(context_info.get("quotedMessage"))
    if not quoted_message:
        return None
    quoted_type = str(next(iter(quoted_message.keys()), ""))
    if quoted_type not in {
        "buttonsMessage",
        "listMessage",
        "templateMessage",
        "interactiveMessage",
        "hydratedTemplate",
    }:
        return None
    return {
        "messageType": quoted_type,
        "raw": quoted_message,
    }


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
    participant_phone = _phone_from_jid(participant) if participant else None
    return {
        "jid": remote_jid or None,
        "isGroup": is_group,
        "groupId": remote_jid if is_group else None,
        "participant": participant or None,
        "participantPhone": participant_phone,
        "participantName": data.get("pushName"),
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
        "contextInfo": context_info or None,
        "interactiveOrigin": _extract_interactive_origin(context_info),
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
    metadata.update(_extract_special_flags(message, message_type, data, context_info))
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
    interaction, interaction_subtype = _extract_interaction(message, message_type)
    if interaction_subtype:
        kind = interaction_subtype
    is_unknown_fallback = kind not in BUSINESS_MESSAGE_TYPES

    text = _extract_text(message)

    from_me = bool(_first(data, ("key", "fromMe")))
    remote_jid = _first(data, ("key", "remoteJid"))
    normalized_subtype = kind if not is_unknown_fallback else "unknown"
    context, metadata = _build_context_and_metadata(data, message, message_type, payload.get("instance"), from_me)
    reaction_content, reaction_target, reaction_meta = _extract_reaction(message)
    location_content, location_meta = _extract_location(message)
    contacts = _extract_contacts(message)
    poll_content, poll_context, poll_meta = _extract_poll(message, message_type)
    normalized_content: Any = {"text": text} if text is not None else {"text": ""}
    if is_unknown_fallback:
        normalized_content = {"text": "[Unsupported message type]"}
    elif interaction and (interaction.get("title") or interaction.get("id")):
        normalized_content = {
            "text": interaction.get("title") or interaction.get("id") or "",
            "interaction": interaction,
        }
    elif kind == "reaction":
        normalized_content = reaction_content or {"emoji": None}
    elif kind == "location":
        normalized_content = location_content or {}
    elif kind == "contact":
        normalized_content = {"contacts": contacts}
    elif kind in {"poll_create", "poll_update"}:
        normalized_content = poll_content or {}

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
        "metadata": {
            **metadata,
            **reaction_meta,
            **location_meta,
            **poll_meta,
            "hasLocation": bool(location_content),
            "hasContacts": bool(contacts),
            "hasPoll": bool(poll_content),
            "unknownTypeDetected": is_unknown_fallback,
        },
        "interaction": interaction,
        "context": {
            **context,
            "targetMessage": reaction_target,
            **({"targetPollId": poll_context.get("targetPollId")} if poll_context and poll_context.get("targetPollId") else {}),
        },
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
    dedupe_key = _event_dedupe_key(normalized)
    if dedupe_key and dedupe_key in _business_event_keys:
        return
    if dedupe_key:
        _business_event_keys_order.append(dedupe_key)
        _business_event_keys.add(dedupe_key)
        while len(_business_event_keys_order) > _business_event_keys_max:
            old = _business_event_keys_order.popleft()
            _business_event_keys.discard(old)
    _business_events.appendleft(normalized)
    media = normalized.get("media")
    message = normalized.get("message") or {}
    if isinstance(media, dict):
        raw_payload = normalized.get("raw") if isinstance(normalized.get("raw"), dict) else {}
        raw_data = raw_payload.get("data") if isinstance(raw_payload.get("data"), dict) else {}
        key_obj = raw_data.get("key") if isinstance(raw_data.get("key"), dict) else {}
        message_obj = raw_data.get("message") if isinstance(raw_data.get("message"), dict) else {}
        _media_index[str(media.get("id"))] = {
            **media,
            "instance": normalized.get("instance"),
            "messageId": message.get("id"),
            "savedAt": int(time.time()),
            "remoteJid": key_obj.get("remoteJid"),
            "fromMe": key_obj.get("fromMe"),
            "participant": key_obj.get("participant"),
            "messageKey": key_obj,
            "messageObject": message_obj,
        }
        logger.info(
            "media_index_store",
            instance=normalized.get("instance"),
            message_id=message.get("id"),
            media_id=media.get("id"),
            stored_key=str(media.get("id") or ""),
            media_key=media.get("mediaKey"),
            file_sha256=((message_obj.get("imageMessage") or {}).get("fileSha256") if isinstance(message_obj, dict) else None)
            or ((message_obj.get("audioMessage") or {}).get("fileSha256") if isinstance(message_obj, dict) else None)
            or ((message_obj.get("videoMessage") or {}).get("fileSha256") if isinstance(message_obj, dict) else None)
            or ((message_obj.get("documentMessage") or {}).get("fileSha256") if isinstance(message_obj, dict) else None),
            direct_path=media.get("directPath"),
        )
        logger.info(
            "media_indexed_probe",
            instance=normalized.get("instance"),
            message_id=message.get("id"),
            media_id=media.get("id"),
            kind=media.get("kind"),
            mime_type=media.get("mimeType"),
            source_url=media.get("url"),
            direct_path=media.get("directPath"),
            has_media_key=bool(media.get("mediaKey")),
        )


def _event_dedupe_key(normalized: dict[str, Any]) -> str | None:
    message = normalized.get("message") if isinstance(normalized.get("message"), dict) else {}
    msg_id = str(message.get("id") or normalized.get("messageId") or "").strip()
    if not msg_id:
        return None
    instance = str(normalized.get("instance") or "").strip()
    event = str(normalized.get("event") or "").strip()
    direction = str(normalized.get("direction") or "").strip()
    subtype = str(normalized.get("subtype") or normalized.get("messageType") or "").strip()
    return "|".join([instance, event, msg_id, direction, subtype])


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
        media = event.get("media")
        if isinstance(media, dict):
            media_id = str(media.get("id") or "")
            tracked = _media_index.get(media_id) if media_id else None
            if isinstance(tracked, dict) and tracked.get("downloadSource"):
                event = {**event, "media": {**media, "downloadSource": tracked.get("downloadSource"), "decryptedSize": tracked.get("decryptedSize")}}
        items.append(event)
        if len(items) >= limit:
            break
    return items


def get_media(media_id: str, *, instance: str | None = None) -> dict[str, Any] | None:
    key = str(media_id or "").strip()
    item = _media_index.get(key)
    if item:
        logger.info(
            "media_index_lookup",
            instance=instance or item.get("instance"),
            lookup_key=key,
            stored_key=key,
            message_id=item.get("messageId"),
            media_id=item.get("id"),
            found=True,
        )
        return item

    logger.info(
        "media_index_lookup",
        instance=instance,
        lookup_key=key,
        stored_key=None,
        message_id=None,
        media_id=None,
        found=False,
    )
    return None


def update_media_download_state(media_id: str, *, source: str, decrypted_size: int | None = None) -> None:
    item = _media_index.get(media_id)
    if not isinstance(item, dict):
        return
    item["downloadSource"] = source
    if decrypted_size is not None:
        item["decryptedSize"] = decrypted_size
