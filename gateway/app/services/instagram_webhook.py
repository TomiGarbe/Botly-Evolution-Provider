from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services.connections import ConnectionNotFoundError, ConnectionService, UnsupportedConnectionProviderError, get_connection_service


class InstagramWebhookError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        provider_account_id: str | None = None,
        connection_lookup_result: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # These values are safe operational identifiers. They deliberately
        # exclude the raw payload, sender identity and any credentials.
        self.provider_account_id = provider_account_id
        self.connection_lookup_result = connection_lookup_result


def process_instagram_webhook(
    payload: dict[str, Any],
    *,
    request_id: str,
    connections: ConnectionService | None = None,
) -> tuple[dict[str, Any], ...]:
    """Resolve and normalize Instagram provider events at the G3/G4 boundary.

    This function deliberately has no dispatcher, persistence into Core, or
    contact/conversation logic.  Its return value is the canonical handoff
    object G4 will own.
    """
    if payload.get("object") != "instagram" or not isinstance(payload.get("entry"), list):
        raise InstagramWebhookError("Invalid Instagram webhook payload", status_code=400)
    service = connections or get_connection_service()
    canonical: list[dict[str, Any]] = []
    for entry in payload["entry"]:
        if not isinstance(entry, dict):
            continue
        # Meta Instagram messaging webhook semantics: entry.id identifies the
        # subscribed professional account. Sender is the external user and is
        # never an account-resolution key.
        provider_account_id = _opaque_id(entry.get("id"))
        if not provider_account_id:
            raise InstagramWebhookError("Instagram webhook entry is missing provider account id", status_code=400)
        connection = _resolve_connection(service, provider_account_id)
        for messaging in entry.get("messaging") or []:
            if not isinstance(messaging, dict):
                continue
            event = _canonical_event(
                messaging,
                request_id=request_id,
                connection_id=connection.id,
                provider_account_id=provider_account_id,
            )
            if event is not None:
                canonical.append(event)
    return tuple(canonical)


def _resolve_connection(service: ConnectionService, provider_account_id: str):
    try:
        return service.resolve_active_instagram_provider_account(provider_account_id)
    except (ConnectionNotFoundError, UnsupportedConnectionProviderError) as exc:
        status_code = 404 if isinstance(exc, ConnectionNotFoundError) else 409
        raise InstagramWebhookError(
            "Instagram connection cannot receive webhooks",
            status_code=status_code,
            provider_account_id=provider_account_id,
            connection_lookup_result="not_found" if isinstance(exc, ConnectionNotFoundError) else "invalid_binding",
        ) from exc


def _canonical_event(
    messaging: dict[str, Any],
    *,
    request_id: str,
    connection_id: str,
    provider_account_id: str,
) -> dict[str, Any] | None:
    sender = _opaque_id(_nested(messaging, "sender", "id"))
    recipient = _opaque_id(_nested(messaging, "recipient", "id")) or provider_account_id
    if not sender:
        return None
    message = messaging.get("message") if isinstance(messaging.get("message"), dict) else None
    if message is not None:
        # Echoes represent the business account's own activity. G3 acknowledges
        # them but does not emit inbound semantics for Core.
        if bool(message.get("is_echo")):
            return None
        return _message_created(
            message,
            sender=sender,
            recipient=recipient,
            source_timestamp=messaging.get("timestamp"),
            request_id=request_id,
            connection_id=connection_id,
            provider_account_id=provider_account_id,
        )
    reaction = messaging.get("reaction") if isinstance(messaging.get("reaction"), dict) else None
    if reaction is not None:
        return _base_event(
            event_type="message.reaction",
            sender=sender,
            recipient=recipient,
            provider_message_id=_opaque_id(reaction.get("mid") or reaction.get("message_id")),
            kind="reaction",
            content=_optional_text(reaction.get("emoji") or reaction.get("reaction")),
            attachments=[],
            source_timestamp=messaging.get("timestamp"),
            request_id=request_id,
            connection_id=connection_id,
            provider_account_id=provider_account_id,
            metadata={"targetProviderMessageId": _opaque_id(reaction.get("mid") or reaction.get("message_id"))},
        )
    postback = messaging.get("postback") if isinstance(messaging.get("postback"), dict) else None
    if postback is not None:
        return _base_event(
            event_type="message.postback",
            sender=sender,
            recipient=recipient,
            provider_message_id=None,
            kind="postback",
            content=_optional_text(postback.get("title") or postback.get("payload")),
            attachments=[],
            source_timestamp=messaging.get("timestamp"),
            request_id=request_id,
            connection_id=connection_id,
            provider_account_id=provider_account_id,
            metadata={"postbackPayload": _optional_text(postback.get("payload"))},
        )
    # Unsupported non-message events are acknowledged and intentionally not
    # dispatched. The HTTP handler emits a safe structured outcome summary.
    return None


def _message_created(
    message: dict[str, Any],
    *,
    sender: str,
    recipient: str,
    source_timestamp: Any,
    request_id: str,
    connection_id: str,
    provider_account_id: str,
) -> dict[str, Any]:
    text = message.get("text") if isinstance(message.get("text"), str) else None
    attachment = _first_attachment(message.get("attachments"))
    attachments = [_canonical_attachment(attachment)] if attachment else []
    kind = "text" if text is not None else _attachment_kind(attachment)
    return _base_event(
        event_type="message.created",
        sender=sender,
        recipient=recipient,
        provider_message_id=_opaque_id(message.get("mid")),
        kind=kind,
        content=text if text is not None else _optional_text(_attachment_payload(attachment).get("caption")),
        attachments=attachments,
        source_timestamp=source_timestamp,
        request_id=request_id,
        connection_id=connection_id,
        provider_account_id=provider_account_id,
        metadata={"providerEventType": "message"},
    )


def _base_event(
    *,
    event_type: str,
    sender: str,
    recipient: str,
    provider_message_id: str | None,
    kind: str,
    content: str | None,
    attachments: list[dict[str, Any]],
    source_timestamp: Any,
    request_id: str,
    connection_id: str,
    provider_account_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    occurred_at, source_time = _occurred_at(source_timestamp)
    return {
        "eventId": str(uuid4()),
        "eventType": event_type,
        "occurredAt": occurred_at,
        "transport": {
            "provider": "meta",
            "channelType": "instagram",
            "connectionRef": connection_id,
            "providerAccountRef": provider_account_id,
        },
        "message": {
            "providerMessageId": provider_message_id,
            "direction": "inbound",
            "kind": kind,
            "content": content,
            "sender": {"externalId": sender},
            "recipient": {"externalId": recipient},
            "attachments": attachments,
        },
        "metadata": {**metadata, "sourceTimestamp": source_time} if source_time is not None else dict(metadata),
        "trace": {"requestId": request_id, "correlationId": request_id},
    }


def _canonical_attachment(attachment: dict[str, Any]) -> dict[str, Any]:
    payload = _attachment_payload(attachment)
    return {
        "kind": _attachment_kind(attachment),
        "providerMediaId": _opaque_id(payload.get("id") or payload.get("attachment_id")),
        "url": _optional_text(payload.get("url")),
        "mimeType": _optional_text(payload.get("mime_type")),
        "fileName": _optional_text(payload.get("name")),
        "size": _int_or_none(payload.get("size")),
        "metadata": {},
    }


def _occurred_at(value: Any) -> tuple[str, str | None]:
    try:
        raw = int(str(value))
        seconds = raw / 1000 if raw > 10_000_000_000 else raw
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), str(value)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), None


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _opaque_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _first_attachment(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), None)
    return None


def _attachment_payload(attachment: dict[str, Any] | None) -> dict[str, Any]:
    return attachment.get("payload") if isinstance(attachment, dict) and isinstance(attachment.get("payload"), dict) else {}


def _attachment_kind(attachment: dict[str, Any] | None) -> str:
    kind = str((attachment or {}).get("type") or "unknown").lower()
    return "document" if kind == "file" else kind


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
