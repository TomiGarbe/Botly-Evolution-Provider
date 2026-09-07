"""Durable canonical-event delivery from Gateway to Botly Core (G4)."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.secret_protection import SecretRedactor
from app.services.connections import ConnectionService, get_connection_service
from app.services.core_channel_credentials import CoreChannelCredentialStore, get_core_channel_credential_store


logger = get_logger(__name__)
_LOCK = threading.Lock()
class CoreInboundPersistenceError(RuntimeError):
    pass


def _now() -> int:
    return int(time.time() * 1000)


def _text(value: Any) -> str:
    # Canonical external IDs are opaque; never normalize or trim their value.
    return str(value) if value is not None else ""


class CoreInboundDeliveryStore:
    """JSON outbox with process locking and atomic replacement.

    This follows the Gateway's existing persistent JSON store convention. It is
    deliberately a durable outbox, not an in-memory background-task queue.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else Path(get_settings().core_inbound_deliveries_path)

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schemaVersion": 1, "deliveries": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self._path.exists():
            return self._empty()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("core_inbound_delivery_store_read_failed", path=str(self._path), error=str(exc))
            return self._empty()
        deliveries = raw.get("deliveries") if isinstance(raw, dict) and isinstance(raw.get("deliveries"), list) else []
        return {"schemaVersion": 1, "deliveries": [item for item in deliveries if isinstance(item, dict)]}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self._path)

    @staticmethod
    def _provider_message_scope(event: dict[str, Any]) -> tuple[str, str, str, str] | None:
        transport = event.get("transport") if isinstance(event.get("transport"), dict) else {}
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        provider_message_id = _text(message.get("providerMessageId"))
        values = (
            _text(transport.get("provider")),
            _text(transport.get("channelType")),
            _text(transport.get("providerAccountRef")),
            provider_message_id,
        )
        return values if all(values) else None

    def enqueue(self, *, event: dict[str, Any], core_channel_id: str | None, initial_error: str | None = None) -> tuple[dict[str, Any], bool]:
        event_id = _text(event.get("eventId"))
        transport = event.get("transport") if isinstance(event.get("transport"), dict) else {}
        if not event_id or not _text(transport.get("provider")) or not _text(transport.get("channelType")) or not _text(transport.get("providerAccountRef")) or not _text(transport.get("connectionRef")):
            raise CoreInboundPersistenceError("Canonical event is missing durable delivery identity")
        scope = self._provider_message_scope(event)
        now = _now()
        delivery = {
            "id": f"core_inbound_{uuid.uuid4().hex}",
            "eventId": event_id,
            "provider": _text(transport.get("provider")),
            "channelType": _text(transport.get("channelType")),
            "providerAccountId": _text(transport.get("providerAccountRef")),
            "connectionId": _text(transport.get("connectionRef")),
            "coreChannelId": _text(core_channel_id) or None,
            "canonicalEvent": deepcopy(event),
            "status": "failed" if initial_error else "pending",
            "attemptCount": 0,
            "nextAttemptAt": None if initial_error else now,
            "lastAttemptAt": None,
            "deliveredAt": None,
            "lastError": initial_error,
            "createdAt": now,
            "updatedAt": now,
            "leaseExpiresAt": None,
            "duplicateAcknowledged": False,
        }
        try:
            with _LOCK:
                data = self._read_unlocked()
                for existing in data["deliveries"]:
                    if _text(existing.get("eventId")) == event_id:
                        return deepcopy(existing), False
                    existing_scope = self._provider_message_scope(existing.get("canonicalEvent") if isinstance(existing.get("canonicalEvent"), dict) else {})
                    if scope is not None and existing_scope == scope:
                        return deepcopy(existing), False
                data["deliveries"].append(delivery)
                self._write_unlocked(data)
        except CoreInboundPersistenceError:
            raise
        except Exception as exc:
            raise CoreInboundPersistenceError("Canonical Core delivery could not be persisted") from exc
        return deepcopy(delivery), True

    def get(self, delivery_id: str) -> dict[str, Any] | None:
        with _LOCK:
            item = next((item for item in self._read_unlocked()["deliveries"] if _text(item.get("id")) == _text(delivery_id)), None)
        return deepcopy(item) if item else None

    def list(self) -> list[dict[str, Any]]:
        with _LOCK:
            return deepcopy(self._read_unlocked()["deliveries"])

    def list_for_connection(self, connection_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return the bounded, connection-scoped view used by operations UI.

        The canonical event itself stays in the durable store, but callers must
        project it before returning it to a browser.  That prevents a future
        canonical-field addition from accidentally becoming an API exposure.
        """
        requested = _text(connection_id)
        deliveries = [item for item in self.list() if _text(item.get("connectionId")) == requested]
        deliveries.sort(key=lambda item: _as_int(item.get("createdAt")), reverse=True)
        return deliveries[:max(1, limit)]

    def claim_due(self, *, limit: int, lease_seconds: int) -> list[dict[str, Any]]:
        now = _now()
        claimed: list[dict[str, Any]] = []
        with _LOCK:
            data = self._read_unlocked()
            for item in sorted(data["deliveries"], key=lambda value: (_as_int(value.get("nextAttemptAt")), _as_int(value.get("createdAt")))):
                if len(claimed) >= max(1, limit):
                    break
                status = _text(item.get("status"))
                due = _as_int(item.get("nextAttemptAt")) <= now
                abandoned = status == "delivering" and _as_int(item.get("leaseExpiresAt")) <= now
                if not ((status in {"pending", "retry"} and due) or abandoned):
                    continue
                item.update({
                    "status": "delivering",
                    "attemptCount": _as_int(item.get("attemptCount")) + 1,
                    "lastAttemptAt": now,
                    "nextAttemptAt": None,
                    "leaseExpiresAt": now + max(1, lease_seconds) * 1000,
                    "updatedAt": now,
                })
                claimed.append(deepcopy(item))
            if claimed:
                self._write_unlocked(data)
        return claimed

    def complete(self, delivery_id: str, *, duplicate_acknowledged: bool = False) -> dict[str, Any]:
        return self._update(delivery_id, {
            "status": "delivered", "deliveredAt": _now(), "nextAttemptAt": None,
            "leaseExpiresAt": None, "lastError": None,
            "duplicateAcknowledged": duplicate_acknowledged,
        })

    def retry_or_dead_letter(self, delivery_id: str, *, error: str, max_attempts: int, backoff_seconds: int) -> dict[str, Any]:
        current = self.get(delivery_id)
        if current is None:
            raise KeyError(delivery_id)
        attempts = _as_int(current.get("attemptCount"))
        if attempts >= max(1, max_attempts):
            return self._update(delivery_id, {
                "status": "dead_letter", "nextAttemptAt": None, "leaseExpiresAt": None, "lastError": error,
            })
        delay = max(0, backoff_seconds) * (2 ** max(0, attempts - 1))
        return self._update(delivery_id, {
            "status": "retry", "nextAttemptAt": _now() + delay * 1000,
            "leaseExpiresAt": None, "lastError": error,
        })

    def fail_permanently(self, delivery_id: str, *, error: str) -> dict[str, Any]:
        return self._update(delivery_id, {"status": "failed", "nextAttemptAt": None, "leaseExpiresAt": None, "lastError": error})

    def _update(self, delivery_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            data = self._read_unlocked()
            for index, item in enumerate(data["deliveries"]):
                if _text(item.get("id")) != _text(delivery_id):
                    continue
                item.update(deepcopy(changes))
                item["updatedAt"] = _now()
                data["deliveries"][index] = item
                self._write_unlocked(data)
                return deepcopy(item)
        raise KeyError(delivery_id)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class CoreInboundDispatcher:
    def __init__(
        self,
        *,
        store: CoreInboundDeliveryStore | None = None,
        connections: ConnectionService | None = None,
        credentials: CoreChannelCredentialStore | None = None,
        settings_factory: Callable[[], Any] = get_settings,
        client_factory: Callable[[float], httpx.AsyncClient] | None = None,
    ) -> None:
        self._store = store or CoreInboundDeliveryStore()
        self._connections = connections or get_connection_service()
        self._credentials = credentials or get_core_channel_credential_store()
        self._settings_factory = settings_factory
        self._client_factory = client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout))

    def persist(self, event: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        transport = event.get("transport") if isinstance(event.get("transport"), dict) else {}
        connection_id = _text(transport.get("connectionRef"))
        try:
            binding = self._connections.instagram_core_channel_binding(connection_id)
        except Exception:
            binding = None
        if binding is None:
            return self._store.enqueue(event=event, core_channel_id=None, initial_error="core_channel_binding_missing")
        return self._store.enqueue(event=event, core_channel_id=binding["channelId"])

    def persist_many(self, events: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        persisted: list[dict[str, Any]] = []
        for event in events:
            delivery, created = self.persist(event)
            persisted.append(delivery)
            logger.info(
                "core_inbound_event_persisted",
                event_id=delivery["eventId"], connection_id=delivery["connectionId"],
                provider_account_id=delivery["providerAccountId"], core_channel_id=delivery.get("coreChannelId"),
                status=delivery["status"], created=created,
            )
        return persisted

    def list_connection_deliveries(self, connection_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Safe operational projection of Instagram's Gateway -> Core outbox."""
        items: list[dict[str, Any]] = []
        for delivery in self._store.list_for_connection(connection_id, limit=limit):
            event = delivery.get("canonicalEvent") if isinstance(delivery.get("canonicalEvent"), dict) else {}
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
            recipient = message.get("recipient") if isinstance(message.get("recipient"), dict) else {}
            trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
            attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
            # Explicit allow-list. Do not return canonical metadata, headers,
            # credentials, or a provider payload from this operator endpoint.
            items.append({
                "id": delivery.get("id"), "event_id": delivery.get("eventId"),
                "provider_account_id": delivery.get("providerAccountId"),
                "core_channel_id": delivery.get("coreChannelId"), "status": delivery.get("status"),
                "attempt_count": delivery.get("attemptCount", 0), "created_at": delivery.get("createdAt"),
                "updated_at": delivery.get("updatedAt"), "last_attempt_at": delivery.get("lastAttemptAt"),
                "delivered_at": delivery.get("deliveredAt"), "last_error": delivery.get("lastError"),
                "duplicate_acknowledged": bool(delivery.get("duplicateAcknowledged")),
                "provider_message_id": message.get("providerMessageId"), "event_type": event.get("eventType"),
                "kind": message.get("kind"), "text": message.get("content"),
                "sender_external_id": sender.get("externalId"), "recipient_external_id": recipient.get("externalId"),
                "attachments": [{key: attachment.get(key) for key in ("kind", "providerMediaId", "mimeType", "fileName", "size")} for attachment in attachments if isinstance(attachment, dict)],
                "request_id": trace.get("requestId"), "correlation_id": trace.get("correlationId"),
            })
        return items

    async def dispatch_due(self) -> int:
        settings = self._settings_factory()
        claimed = self._store.claim_due(
            limit=max(1, int(getattr(settings, "core_inbound_delivery_batch_size", 25))),
            lease_seconds=max(1, int(getattr(settings, "core_inbound_delivery_lease_seconds", 60))),
        )
        for delivery in claimed:
            await self._deliver(delivery, settings)
        return len(claimed)

    async def _deliver(self, delivery: dict[str, Any], settings: Any) -> None:
        delivery_id = _text(delivery.get("id"))
        event_id = _text(delivery.get("eventId"))
        core_channel_id = _text(delivery.get("coreChannelId"))
        connection_id = _text(delivery.get("connectionId"))
        url = _text(getattr(settings, "core_inbound_url", ""))
        if not url:
            self._permanent(delivery, "core_inbound_url_missing")
            return
        if not core_channel_id:
            self._permanent(delivery, "core_channel_binding_missing")
            return
        try:
            api_key = self._credentials.get_api_key(connection_id=connection_id, core_channel_id=core_channel_id)
        except Exception:
            api_key = None
        if not api_key:
            self._permanent(delivery, "core_channel_credential_missing")
            return
        logger.info(
            "[INSTAGRAM][CORE_DISPATCH_STARTED] canonical delivery attempt",
            event_id=event_id,
            connection_id=connection_id,
            provider_account_id=delivery["providerAccountId"],
            core_channel_id=core_channel_id,
            request_id=((delivery.get("canonicalEvent") or {}).get("trace") or {}).get("requestId"),
            correlation_id=((delivery.get("canonicalEvent") or {}).get("trace") or {}).get("correlationId"),
            provider_message_id=((delivery.get("canonicalEvent") or {}).get("message") or {}).get("providerMessageId"),
            attempt=delivery["attemptCount"],
            status="delivering",
        )
        started = time.perf_counter()
        try:
            async with self._client_factory(float(getattr(settings, "bot_webhook_timeout", 5))) as client:
                response = await client.post(
                    url,
                    json=delivery["canonicalEvent"],
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "X-Botly-Contract-Version": "canonical-v1",
                        "Content-Type": "application/json",
                    },
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self._retry(delivery, f"transport:{exc.__class__.__name__}", settings)
            return
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if 200 <= response.status_code < 300:
            self._store.complete(delivery_id)
            logger.info("[INSTAGRAM][CORE_DISPATCH_SUCCESS] canonical delivery acknowledged", event_id=event_id, connection_id=connection_id, provider_account_id=delivery["providerAccountId"], core_channel_id=core_channel_id, request_id=((delivery.get("canonicalEvent") or {}).get("trace") or {}).get("requestId"), correlation_id=((delivery.get("canonicalEvent") or {}).get("trace") or {}).get("correlationId"), provider_message_id=((delivery.get("canonicalEvent") or {}).get("message") or {}).get("providerMessageId"), attempt=delivery["attemptCount"], status=response.status_code, latency_ms=elapsed_ms)
            return
        # B4's canonical inbound endpoint treats an idempotent conflict as an
        # already-processed event. It is a logical delivery success, not a new
        # domain message.
        if response.status_code == 409:
            self._store.complete(delivery_id, duplicate_acknowledged=True)
            logger.info("core_inbound_delivery_duplicate", event_id=event_id, connection_id=connection_id, provider_account_id=delivery["providerAccountId"], core_channel_id=core_channel_id, attempt=delivery["attemptCount"], status=409, latency_ms=elapsed_ms)
            return
        error = f"http_{response.status_code}"
        if response.status_code in {400, 401, 403, 404, 405, 410, 422}:
            self._permanent(delivery, error)
            return
        self._retry(delivery, error, settings)

    def _retry(self, delivery: dict[str, Any], error: str, settings: Any) -> None:
        result = self._store.retry_or_dead_letter(
            _text(delivery.get("id")), error=error,
            max_attempts=max(1, int(getattr(settings, "core_inbound_delivery_max_attempts", 5))),
            backoff_seconds=max(0, int(getattr(settings, "core_inbound_delivery_backoff_base_seconds", 5))),
        )
        canonical = result.get("canonicalEvent") if isinstance(result.get("canonicalEvent"), dict) else {}
        logger.warning("[INSTAGRAM][CORE_DISPATCH_FAILED] canonical delivery will retry" if result["status"] == "retry" else "[INSTAGRAM][CORE_DISPATCH_FAILED] canonical delivery dead-lettered", event_id=result["eventId"], connection_id=result["connectionId"], provider_account_id=result["providerAccountId"], core_channel_id=result.get("coreChannelId"), request_id=((canonical.get("trace") or {}).get("requestId")), correlation_id=((canonical.get("trace") or {}).get("correlationId")), provider_message_id=((canonical.get("message") or {}).get("providerMessageId")), attempt=result["attemptCount"], status=result["status"], error=error)

    def _permanent(self, delivery: dict[str, Any], error: str) -> None:
        result = self._store.fail_permanently(_text(delivery.get("id")), error=error)
        canonical = result.get("canonicalEvent") if isinstance(result.get("canonicalEvent"), dict) else {}
        logger.warning("[INSTAGRAM][CORE_DISPATCH_FAILED] canonical delivery permanently rejected", event_id=result["eventId"], connection_id=result["connectionId"], provider_account_id=result["providerAccountId"], core_channel_id=result.get("coreChannelId"), request_id=((canonical.get("trace") or {}).get("requestId")), correlation_id=((canonical.get("trace") or {}).get("correlationId")), provider_message_id=((canonical.get("message") or {}).get("providerMessageId")), attempt=result["attemptCount"], status=result["status"], error=error)


class CoreInboundDeliveryWorker:
    def __init__(self, dispatcher: CoreInboundDispatcher | None = None) -> None:
        self._dispatcher = dispatcher or CoreInboundDispatcher()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None and bool(getattr(get_settings(), "core_inbound_dispatcher_enabled", True)):
            self._task = asyncio.create_task(self._run(), name="core-inbound-delivery-worker")

    async def _run(self) -> None:
        while not self._stopping:
            try:
                processed = await self._dispatcher.dispatch_due()
                await asyncio.sleep(0.1 if processed else max(1, int(getattr(get_settings(), "core_inbound_delivery_poll_seconds", 2))))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("core_inbound_delivery_worker_tick_failed", error=SecretRedactor.redact_json_preview(str(exc), max_chars=300))
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


_dispatcher = CoreInboundDispatcher()


def get_core_inbound_dispatcher() -> CoreInboundDispatcher:
    return _dispatcher
