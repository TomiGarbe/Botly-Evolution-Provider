"""Durable, secret-safe evidence for outbound provider side effects.

The store is intentionally independent from the timeline: an attempt must be
durable *before* a provider POST and must survive if the process dies before a
business event can be written.  It is not an outbox, retry queue, or provider
idempotency implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from app.core.config import get_settings
from app.core.secret_protection import SecretRedactor
from app.services.connection_registry import get_connection_registry


_LOCK = threading.Lock()
_SCHEMA_VERSION = 1
_T = TypeVar("_T")


class OutboundAttemptPersistenceError(RuntimeError):
    """The side effect must not run because its evidence could not be stored."""


def _now() -> int:
    return int(time.time() * 1000)


def _safe_text(value: Any, limit: int = 4096) -> str | None:
    text = str(value or "")
    return text[:limit] if text else None


def _safe_media_reference(media: dict[str, Any] | None) -> dict[str, Any] | None:
    if not media:
        return None
    # Source bytes, base64, URLs (including signed URLs), media keys and tokens
    # deliberately do not cross this boundary.
    return {
        "status": "not_reconstructable",
        "kind": _safe_text(media.get("kind"), 64),
        "mimeType": _safe_text(media.get("mimeType"), 256),
        "fileName": _safe_text(media.get("fileName"), 512),
        "source": _safe_text(media.get("source"), 32),
    }


def _same_canonical_idempotency_scope(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Compare only the explicit canonical delivery identity.

    A recipient remains an opaque external identifier here.  Do not derive a
    phone number, JID, or provider-specific equivalent for deduplication.
    """
    return (
        str(existing.get("canonicalChannelId") or "") == str(candidate.get("canonicalChannelId") or "")
        # New canonical attempts retain the pre-mapping Core identifier.  The
        # fallback preserves the behavior of records written before this field
        # existed, while all new attempts compare the exact opaque externalId.
        and str(existing.get("canonicalRecipientExternalId") or existing.get("recipient") or "")
        == str(candidate.get("canonicalRecipientExternalId") or candidate.get("recipient") or "")
        and str(existing.get("idempotencyKey") or "") == str(candidate.get("idempotencyKey") or "")
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(SecretRedactor.redact_json(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _error_state(exc: Exception) -> tuple[str, str, str, str, int | None]:
    """Classify only evidence available to the Gateway; never infer no side effect."""
    status_code = getattr(exc, "status_code", None)
    status_code = status_code if isinstance(status_code, int) else None
    message = SecretRedactor.redact_json_preview(str(exc), max_chars=500)
    lowered = message.lower()
    if "timeout" in lowered or status_code == 504:
        return "timeout", "unknown", "pending", "timeout", status_code
    if status_code == 502 or any(token in lowered for token in ("transport", "network", "connect", "dns", "ssl", "reset")):
        return "network_error", "unknown", "pending", "network_error", status_code
    if status_code in {408, 429} or (status_code is not None and status_code >= 500):
        return "failed", "unknown", "pending", "http_error", status_code
    if status_code == 422 and any(token in lowered for token in ("credential", "token", "config")):
        return "configuration_error", "failed", "not_required", "configuration_error", status_code
    if status_code is not None and 400 <= status_code < 500:
        return "failed", "failed", "not_required", "http_error", status_code
    # Unknown includes unexpected process/runtime failures after the persisted
    # intent. It is deliberately conservative for future reconciliation.
    return "unknown", "unknown", "pending", "unknown", status_code


class OutboundProviderAttemptStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else Path(get_settings().outbound_provider_attempts_path)

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schemaVersion": _SCHEMA_VERSION, "attempts": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self._path.exists():
            return self._empty()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty()
        attempts = data.get("attempts") if isinstance(data, dict) else None
        return {"schemaVersion": _SCHEMA_VERSION, "attempts": [item for item in attempts if isinstance(item, dict)] if isinstance(attempts, list) else []}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(SecretRedactor.redact_json(data), ensure_ascii=True, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self._path)

    def create(
        self, *, instance: str, provider: str, message_type: str, recipient: str | None,
        text: str | None = None, caption: str | None = None, correlation_id: str | None = None,
        request_id: str | None = None, operation: str = "provider.message.outbound",
        provider_operation: str | None = None, media: dict[str, Any] | None = None,
        message_id: str | None = None, conversation_id: str | None = None,
        idempotency_key: str | None = None,
        canonical_channel_id: str | None = None,
        canonical_recipient_external_id: str | None = None,
        retry_of_attempt_id: str | None = None, triggered_by_manual_action_id: str | None = None,
        source_delivery_id: str | None = None,
    ) -> dict[str, Any]:
        attempt = self._build_attempt(
            instance=instance,
            provider=provider,
            message_type=message_type,
            recipient=recipient,
            text=text,
            caption=caption,
            correlation_id=correlation_id,
            request_id=request_id,
            operation=operation,
            provider_operation=provider_operation,
            media=media,
            message_id=message_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            canonical_channel_id=canonical_channel_id,
            canonical_recipient_external_id=canonical_recipient_external_id,
            retry_of_attempt_id=retry_of_attempt_id,
            triggered_by_manual_action_id=triggered_by_manual_action_id,
            source_delivery_id=source_delivery_id,
        )
        try:
            with _LOCK:
                data = self._read_unlocked()
                data["attempts"].append(attempt)
                self._trim_and_write_unlocked(data)
        except Exception as exc:
            raise OutboundAttemptPersistenceError("No se pudo persistir el intento outbound") from exc
        return deepcopy(attempt)

    def create_or_get_by_idempotency(
        self, *, instance: str, provider: str, message_type: str, recipient: str | None,
        text: str | None = None, caption: str | None = None, correlation_id: str | None = None,
        request_id: str | None = None, operation: str = "provider.message.outbound",
        provider_operation: str | None = None, media: dict[str, Any] | None = None,
        message_id: str | None = None, conversation_id: str | None = None,
        idempotency_key: str,
        canonical_channel_id: str,
        canonical_recipient_external_id: str,
        retry_of_attempt_id: str | None = None, triggered_by_manual_action_id: str | None = None,
        source_delivery_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically claim a canonical outbound idempotency scope.

        The durable key is deliberately scoped to the Core Channel and opaque
        recipient, rather than a provider-specific phone/JID representation.
        The same process-wide persistence lock guards lookup and insertion, so
        concurrent canonical requests cannot both issue provider sends.
        """
        clean_key = _safe_text(idempotency_key, 128)
        clean_channel_id = _safe_text(canonical_channel_id, 256)
        if not clean_key or not clean_channel_id:
            raise ValueError("canonical idempotency key and channel are required")
        attempt = self._build_attempt(
            instance=instance,
            provider=provider,
            message_type=message_type,
            recipient=recipient,
            text=text,
            caption=caption,
            correlation_id=correlation_id,
            request_id=request_id,
            operation=operation,
            provider_operation=provider_operation,
            media=media,
            message_id=message_id,
            conversation_id=conversation_id,
            idempotency_key=clean_key,
            canonical_channel_id=clean_channel_id,
            canonical_recipient_external_id=canonical_recipient_external_id,
            retry_of_attempt_id=retry_of_attempt_id,
            triggered_by_manual_action_id=triggered_by_manual_action_id,
            source_delivery_id=source_delivery_id,
        )
        try:
            with _LOCK:
                data = self._read_unlocked()
                for existing in data["attempts"]:
                    if _same_canonical_idempotency_scope(existing, attempt):
                        return deepcopy(existing), False
                data["attempts"].append(attempt)
                self._trim_and_write_unlocked(data)
        except Exception as exc:
            raise OutboundAttemptPersistenceError("No se pudo persistir el intento outbound") from exc
        return deepcopy(attempt), True

    def _build_attempt(
        self, *, instance: str, provider: str, message_type: str, recipient: str | None,
        text: str | None, caption: str | None, correlation_id: str | None,
        request_id: str | None, operation: str, provider_operation: str | None,
        media: dict[str, Any] | None, message_id: str | None, conversation_id: str | None,
        idempotency_key: str | None, canonical_channel_id: str | None,
        canonical_recipient_external_id: str | None,
        retry_of_attempt_id: str | None, triggered_by_manual_action_id: str | None,
        source_delivery_id: str | None,
    ) -> dict[str, Any]:
        registry_record = get_connection_registry().connection_record(instance) or {}
        connection_id = str(registry_record.get("id") or "").strip() or None
        channel_id = str(registry_record.get("channel_id") or "").strip() or None
        request_payload = {
            "messageType": _safe_text(message_type, 64), "recipient": _safe_text(recipient, 128),
            "text": _safe_text(text), "caption": _safe_text(caption),
            "providerOperation": _safe_text(provider_operation, 128),
            "media": _safe_media_reference(media),
        }
        now = _now()
        attempt = {
            "id": f"outbound_attempt_{uuid.uuid4().hex}", "attemptId": None,
            "instance": instance, "connectionId": connection_id, "provider": provider,
            "direction": "outbound", "operation": operation, "createdAt": now, "startedAt": now,
            "finishedAt": None, "messageId": _safe_text(message_id, 256),
            "conversationId": _safe_text(conversation_id, 256), "channelId": channel_id,
            "correlationId": _safe_text(correlation_id, 256), "requestId": _safe_text(request_id, 256),
            "providerMessageId": None, "recipient": _safe_text(recipient, 255),
            "messageType": _safe_text(message_type, 64), "semanticStatus": "unknown",
            "attemptState": "pending", "deliveryState": "pending", "reconciliationState": "pending",
            "error": None, "requestMetadata": {"providerOperation": _safe_text(provider_operation, 128), "hasText": bool(text), "hasCaption": bool(caption)},
            "requestPayload": request_payload, "requestFingerprint": _fingerprint(request_payload),
            "mediaReference": _safe_media_reference(media), "idempotencyKey": _safe_text(idempotency_key, 255),
            "canonicalChannelId": _safe_text(canonical_channel_id, 256),
            "canonicalRecipientExternalId": _safe_text(canonical_recipient_external_id, 255),
            "retryOf": _safe_text(retry_of_attempt_id, 256),
            "triggeredByManualActionId": _safe_text(triggered_by_manual_action_id, 256),
            "sourceDeliveryId": _safe_text(source_delivery_id, 256), "legacy": False,
        }
        attempt["attemptId"] = attempt["id"]
        return attempt

    def _trim_and_write_unlocked(self, data: dict[str, Any]) -> None:
        limit = max(1, int(getattr(get_settings(), "outbound_provider_attempt_retention", 2000)))
        data["attempts"] = data["attempts"][-limit:]
        self._write_unlocked(data)

    def finish_success(self, attempt_id: str, result: dict[str, Any] | None) -> dict[str, Any]:
        provider_message_id = _provider_message_id(result)
        return self._update(attempt_id, {
            "finishedAt": _now(), "attemptState": "completed", "semanticStatus": "success",
            "deliveryState": "accepted", "reconciliationState": "not_required",
            "providerMessageId": provider_message_id, "error": None,
        })

    def finish_error(self, attempt_id: str, exc: Exception) -> dict[str, Any]:
        semantic, delivery, reconciliation, category, status = _error_state(exc)
        return self._update(attempt_id, {
            "finishedAt": _now(), "attemptState": "completed", "semanticStatus": semantic,
            "deliveryState": delivery, "reconciliationState": reconciliation,
            "error": {"category": category, "message": SecretRedactor.redact_json_preview(str(exc), max_chars=500), "httpStatus": status},
        })

    def _update(self, attempt_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            data = self._read_unlocked()
            for index, item in enumerate(data["attempts"]):
                if str(item.get("id")) == attempt_id:
                    item.update(SecretRedactor.redact_json(changes))
                    data["attempts"][index] = item
                    self._write_unlocked(data)
                    return deepcopy(item)
        raise KeyError(attempt_id)

    def list(self, *, instance: str | None = None) -> list[dict[str, Any]]:
        with _LOCK:
            items = self._read_unlocked()["attempts"]
        return [deepcopy(item) for item in items if instance is None or item.get("instance") == instance]

    def get(self, attempt_id: str) -> dict[str, Any] | None:
        wanted = str(attempt_id or "").strip()
        if not wanted:
            return None
        with _LOCK:
            for item in self._read_unlocked()["attempts"]:
                if str(item.get("id") or "") == wanted:
                    return deepcopy(item)
        return None

    def record_reconciliation(
        self, *, attempt_id: str, evidence: dict[str, Any], delivery_state: str | None = None,
        resolved: bool = False,
    ) -> dict[str, Any]:
        """Append bounded, redacted reconciliation evidence to one attempt."""
        safe_evidence = SecretRedactor.redact_json(evidence)
        with _LOCK:
            data = self._read_unlocked()
            for index, item in enumerate(data["attempts"]):
                if str(item.get("id") or "") != attempt_id:
                    continue
                history = item.get("reconciliationHistory") if isinstance(item.get("reconciliationHistory"), list) else []
                history = [entry for entry in history if isinstance(entry, dict)][-49:] + [safe_evidence]
                changes: dict[str, Any] = {
                    "lastReconciliation": safe_evidence,
                    "reconciliationHistory": history,
                }
                if delivery_state:
                    changes["deliveryState"] = _safe_text(delivery_state, 64)
                if resolved:
                    changes["reconciliationState"] = "not_required"
                item.update(SecretRedactor.redact_json(changes))
                data["attempts"][index] = item
                self._write_unlocked(data)
                return deepcopy(item)
        raise KeyError(attempt_id)

    def provider_message_candidates(
        self, *, instance: str, provider: str, provider_message_id: str,
    ) -> list[dict[str, Any]]:
        """Return only exact, tenant-scoped provider message matches.

        This is deliberately not a heuristic lookup: recipient, text, time and
        fingerprints are never considered evidence for status correlation.
        """
        clean_instance = str(instance or "").strip()
        clean_provider = str(provider or "").strip().lower()
        clean_message_id = str(provider_message_id or "").strip()
        if not clean_instance or not clean_provider or not clean_message_id:
            return []
        return [
            item for item in self.list(instance=clean_instance)
            if str(item.get("provider") or "").strip().lower() == clean_provider
            and str(item.get("providerMessageId") or "").strip() == clean_message_id
        ]

    def record_provider_status(
        self, *, attempt_id: str, status: str, provider: str,
        provider_message_id: str, event_id: str | None, timestamp: int | None,
    ) -> dict[str, Any]:
        """Attach local webhook evidence without altering the technical result.

        A provider status confirms delivery progress, while ``semanticStatus``
        remains the outcome of the original provider request.  History keeps
        the update non-destructive and makes repeated webhook evidence visible.
        """
        safe_status = _safe_text(status, 64)
        if not safe_status:
            raise ValueError("A provider status is required")
        evidence = {
            "eventId": _safe_text(event_id, 256),
            "timestamp": timestamp if isinstance(timestamp, int) else _now(),
            "provider": _safe_text(provider, 64),
            "providerMessageId": _safe_text(provider_message_id, 256),
            "status": safe_status,
        }
        with _LOCK:
            data = self._read_unlocked()
            for index, item in enumerate(data["attempts"]):
                if str(item.get("id")) != attempt_id:
                    continue
                history = item.get("statusHistory") if isinstance(item.get("statusHistory"), list) else []
                if evidence not in history:
                    history = [entry for entry in history if isinstance(entry, dict)][-49:] + [evidence]
                item.update(SecretRedactor.redact_json({
                    "deliveryState": safe_status,
                    "reconciliationState": "not_required",
                    "lastProviderStatus": evidence,
                    "statusHistory": history,
                }))
                data["attempts"][index] = item
                self._write_unlocked(data)
                return deepcopy(item)
        raise KeyError(attempt_id)


def _provider_message_id(result: dict[str, Any] | None) -> str | None:
    result = result if isinstance(result, dict) else {}
    key = result.get("key") if isinstance(result.get("key"), dict) else {}
    message = result.get("message") if isinstance(result.get("message"), dict) else {}
    nested_key = message.get("key") if isinstance(message.get("key"), dict) else {}
    value = result.get("providerMessageId") or result.get("messageId") or key.get("id") or nested_key.get("id")
    return str(value).strip() or None if value is not None else None


_store = OutboundProviderAttemptStore()


def get_outbound_provider_attempt_store() -> OutboundProviderAttemptStore:
    return _store


def provider_delivery_from_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    """Project durable attempt evidence into the existing delivery read model."""
    return SecretRedactor.redact_json({
        "id": attempt.get("id"), "attemptId": attempt.get("id"), "timestamp": attempt.get("createdAt"),
        "direction": "outbound", "operation": attempt.get("operation"), "provider": attempt.get("provider"),
        "semanticStatus": attempt.get("semanticStatus"), "deliveryState": attempt.get("deliveryState"),
        "reconciliationState": attempt.get("reconciliationState"), "messageId": attempt.get("messageId"),
        "providerMessageId": attempt.get("providerMessageId"), "conversationId": attempt.get("conversationId"),
        "channelId": attempt.get("channelId"), "connectionId": attempt.get("connectionId"),
        "correlationId": attempt.get("correlationId"), "requestId": attempt.get("requestId"),
        "eventId": None, "durationMs": None, "attemptCount": 1, "retryCount": 0,
        "request": {"method": "PROVIDER_SEND", "body": attempt.get("requestPayload") or {}},
        "response": {"status": None, "headers": {}, "body": {}}, "error": attempt.get("error"),
        "metadata": {
            "attemptState": attempt.get("attemptState"), "requestFingerprint": attempt.get("requestFingerprint"),
            "mediaReference": attempt.get("mediaReference"), "lastProviderStatus": attempt.get("lastProviderStatus"),
            "statusHistory": attempt.get("statusHistory") or [], "lastReconciliation": attempt.get("lastReconciliation"),
            "reconciliationHistory": attempt.get("reconciliationHistory") or [], "retryOfAttemptId": attempt.get("retryOf"),
            "triggeredByManualActionId": attempt.get("triggeredByManualActionId"), "sourceDeliveryId": attempt.get("sourceDeliveryId"),
            "legacy": False,
        },
        "isTest": False,
    })


async def execute_outbound_attempt(
    *, attempt: dict[str, Any], sender: Callable[[], Awaitable[_T]], store: OutboundProviderAttemptStore | None = None,
) -> tuple[_T, dict[str, Any]]:
    """Run a provider call only after a durable attempt exists."""
    active_store = store or _store
    try:
        result = await sender()
    except Exception as exc:
        active_store.finish_error(str(attempt["id"]), exc)
        raise
    finalized = active_store.finish_success(str(attempt["id"]), result if isinstance(result, dict) else None)
    return result, finalized
