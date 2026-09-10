"""Provider-neutral canonical outbound delivery owned by Botly Gateway."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.connections import get_connection_manager
from app.core.logging import get_logger
from app.models.canonical_outbound import CanonicalOutboundMessage
from app.platforms.meta import MetaPlatformError
from app.providers.defaults import get_default_provider_registry
from app.providers.instagram import MetaInstagramProvider
from app.providers.registry import ProviderRegistry
from app.services.connection_registry import ConnectionRegistry, get_connection_registry
from app.services.connections import (
    ConnectionNotFoundError,
    ConnectionService,
    UnsupportedConnectionProviderError,
    get_connection_service,
)
from app.services.outbound_provider_attempts import (
    OutboundProviderAttemptStore,
    execute_outbound_attempt,
    get_outbound_provider_attempt_store,
)


logger = get_logger(__name__)

_EVOLUTION_WHATSAPP = ("evolution", "whatsapp")
_META_INSTAGRAM = ("meta", "instagram")


class CanonicalOutboundError(Exception):
    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ResolvedOutboundConnection:
    connection_id: str
    runtime_name: str
    provider: str
    channel_type: str


class CanonicalOutboundService:
    def __init__(
        self,
        *,
        registry: ConnectionRegistry | None = None,
        connection_manager: Any | None = None,
        connection_service: ConnectionService | None = None,
        provider_registry: ProviderRegistry | None = None,
        attempt_store: OutboundProviderAttemptStore | None = None,
    ) -> None:
        self._registry = registry or get_connection_registry()
        self._connection_manager = connection_manager or get_connection_manager()
        self._connection_service = connection_service or get_connection_service()
        self._provider_registry = provider_registry or get_default_provider_registry()
        self._attempt_store = attempt_store

    def resolve_connection(self, *, message: CanonicalOutboundMessage, authenticated_instance: str | None) -> ResolvedOutboundConnection:
        scoped_instance = str(authenticated_instance or "").strip()
        if scoped_instance:
            record = self._registry.connection_record(scoped_instance)
            if record is None:
                raise CanonicalOutboundError("Authenticated Gateway connection was not found.", status_code=404, code="connection_not_found")
            binding = record.get("core_channel") if isinstance(record.get("core_channel"), dict) else {}
            bound_channel_id = str(binding.get("channelId") or "").strip()
            if bound_channel_id and bound_channel_id != str(message.channel.id):
                raise CanonicalOutboundError("Canonical channel does not match the authenticated connection.", status_code=409, code="channel_connection_mismatch")
        else:
            matches = [
                record for record in self._registry.connection_records()
                if isinstance(record.get("core_channel"), dict)
                and str(record["core_channel"].get("channelId") or "") == str(message.channel.id)
            ]
            if len(matches) != 1:
                raise CanonicalOutboundError("Canonical channel is not uniquely bound to a Gateway connection.", status_code=409, code="channel_binding_missing")
            record = matches[0]

        provider = str(record.get("provider_id") or "").strip().lower()
        channel_type = str(record.get("channel_id") or "").strip().lower()
        runtime_name = str(record.get("legacy_name") or "").strip()
        connection_id = str(record.get("id") or "").strip()
        if not runtime_name or provider != message.channel.provider or channel_type != message.channel.channel_type:
            raise CanonicalOutboundError("Canonical transport does not match the resolved Gateway connection.", status_code=409, code="transport_mismatch")
        return ResolvedOutboundConnection(
            connection_id=connection_id,
            runtime_name=runtime_name,
            provider=provider,
            channel_type=channel_type,
        )

    async def deliver(self, *, message: CanonicalOutboundMessage, authenticated_instance: str | None) -> dict[str, Any]:
        logger.info(
            "canonical_outbound_received",
            channel_id=str(message.channel.id),
            channel_type=message.channel.channel_type,
            provider=message.channel.provider,
            message_kind=message.message.kind,
            idempotency_key=message.idempotency_key,
            correlation_id=message.trace.correlation_id,
        )
        connection = self.resolve_connection(message=message, authenticated_instance=authenticated_instance)
        transport = (connection.provider, connection.channel_type)
        sender = self._sender_for(transport=transport, connection=connection, message=message)
        recipient = _recipient_for_transport(transport, message.recipient.external_id)
        attachment = _validate_message_for_transport(transport, message)
        correlation_id = message.trace.correlation_id or message.trace.request_id
        logger.info(
            "canonical_outbound_connection_resolved",
            connection_id=connection.connection_id,
            channel_id=str(message.channel.id),
            runtime_name=connection.runtime_name,
            provider=connection.provider,
            channel_type=connection.channel_type,
            correlation_id=correlation_id,
        )
        logger.info(
            "canonical_outbound_adapter_selected",
            connection_id=connection.connection_id,
            channel_id=str(message.channel.id),
            provider=connection.provider,
            channel_type=connection.channel_type,
            correlation_id=correlation_id,
        )

        attempt_store = self._attempt_store or get_outbound_provider_attempt_store()
        attempt, created = attempt_store.create_or_get_by_idempotency(
            instance=connection.runtime_name,
            provider=connection.provider,
            message_type=message.message.kind,
            recipient=recipient,
            text=message.message.content,
            correlation_id=correlation_id,
            request_id=message.trace.request_id,
            provider_operation="canonical.outbound.sendText" if message.message.kind == "text" else "canonical.outbound.sendMedia",
            media={
                "kind": message.message.kind,
                "mimeType": attachment.mime_type if attachment else None,
                "fileName": attachment.filename if attachment else None,
                "source": "canonical_attachment",
            } if attachment else None,
            idempotency_key=message.idempotency_key,
            canonical_channel_id=str(message.channel.id),
            canonical_recipient_external_id=message.recipient.external_id,
        )
        if not created:
            return self._duplicate_result(attempt=attempt, message=message, connection=connection)

        logger.info(
            "canonical_outbound_dispatch_started",
            connection_id=connection.connection_id,
            channel_id=str(message.channel.id),
            provider=connection.provider,
            channel_type=connection.channel_type,
            idempotency_key=message.idempotency_key,
            correlation_id=correlation_id,
        )
        try:
            _result, finalized = await execute_outbound_attempt(attempt=attempt, sender=sender, store=attempt_store)
        except CanonicalOutboundError:
            self._log_dispatch_failed(connection=connection, message=message, correlation_id=correlation_id, status_code=None)
            raise
        except MetaPlatformError as exc:
            self._log_dispatch_failed(connection=connection, message=message, correlation_id=correlation_id, status_code=exc.status_code)
            raise CanonicalOutboundError("Provider rejected canonical outbound delivery.", status_code=exc.status_code, code="provider_delivery_failed") from exc
        except (ConnectionNotFoundError, UnsupportedConnectionProviderError, ValueError, RuntimeError) as exc:
            if transport == _META_INSTAGRAM:
                self._log_dispatch_failed(connection=connection, message=message, correlation_id=correlation_id, status_code=422)
                raise CanonicalOutboundError("Instagram outbound connection is not ready.", status_code=422, code="provider_configuration_invalid") from exc
            self._log_dispatch_failed(connection=connection, message=message, correlation_id=correlation_id, status_code=502)
            raise CanonicalOutboundError("Provider delivery failed.", status_code=502, code="provider_delivery_failed") from exc
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            safe_status = status_code if isinstance(status_code, int) else 502
            self._log_dispatch_failed(connection=connection, message=message, correlation_id=correlation_id, status_code=safe_status)
            raise CanonicalOutboundError("Provider delivery failed.", status_code=safe_status, code="provider_delivery_failed") from exc

        provider_message_id = finalized.get("providerMessageId")
        logger.info(
            "canonical_outbound_dispatch_success",
            connection_id=connection.connection_id,
            channel_id=str(message.channel.id),
            provider=connection.provider,
            channel_type=connection.channel_type,
            provider_message_id=provider_message_id,
            idempotency_key=message.idempotency_key,
            correlation_id=correlation_id,
        )
        return self._accepted_result(message=message, connection=connection, provider_message_id=provider_message_id)

    def _sender_for(
        self,
        *,
        transport: tuple[str, str],
        connection: ResolvedOutboundConnection,
        message: CanonicalOutboundMessage,
    ) -> Callable[[], Awaitable[dict[str, Any]]]:
        senders: dict[tuple[str, str], Callable[[], Awaitable[dict[str, Any]]]] = {
            _EVOLUTION_WHATSAPP: lambda: self._send_evolution(connection=connection, message=message),
            _META_INSTAGRAM: lambda: self._send_instagram(connection=connection, message=message),
        }
        sender = senders.get(transport)
        if sender is None:
            raise CanonicalOutboundError("No canonical outbound adapter is enabled for this provider/channel.", status_code=501, code="provider_not_implemented")
        return sender

    async def _send_evolution(self, *, connection: ResolvedOutboundConnection, message: CanonicalOutboundMessage) -> dict[str, Any]:
        number = _recipient_for_transport(_EVOLUTION_WHATSAPP, message.recipient.external_id)
        attachment = _validate_message_for_transport(_EVOLUTION_WHATSAPP, message)
        if message.message.kind == "text":
            return await self._connection_manager.send_text(connection.runtime_name, number, message.message.content or "")
        assert attachment is not None
        return await self._connection_manager.send_media(
            connection.runtime_name,
            number,
            attachment.url or "",
            message.message.kind,
            attachment.mime_type or "application/octet-stream",
            attachment.filename or "file.bin",
            message.message.content or "",
        )

    async def _send_instagram(self, *, connection: ResolvedOutboundConnection, message: CanonicalOutboundMessage) -> dict[str, Any]:
        if not connection.connection_id:
            raise CanonicalOutboundError("Instagram connection is missing its Gateway identity.", status_code=422, code="provider_configuration_invalid")
        adapter = self._provider_registry.get(provider_id="meta", channel_type="instagram")
        if not isinstance(adapter, MetaInstagramProvider):
            raise CanonicalOutboundError("No canonical outbound adapter is enabled for this provider/channel.", status_code=501, code="provider_not_implemented")
        return await self._connection_service.send_instagram_text(
            connection_id=connection.connection_id,
            external_id=message.recipient.external_id,
            text=message.message.content or "",
            provider=adapter,
        )

    def _duplicate_result(
        self,
        *,
        attempt: dict[str, Any],
        message: CanonicalOutboundMessage,
        connection: ResolvedOutboundConnection,
    ) -> dict[str, Any]:
        if attempt.get("semanticStatus") == "success" and attempt.get("deliveryState") == "accepted":
            logger.info(
                "canonical_outbound_idempotency_reused",
                connection_id=connection.connection_id,
                channel_id=str(message.channel.id),
                provider=connection.provider,
                idempotency_key=message.idempotency_key,
                provider_message_id=attempt.get("providerMessageId"),
            )
            return self._accepted_result(message=message, connection=connection, provider_message_id=attempt.get("providerMessageId"))
        raise CanonicalOutboundError(
            "A canonical outbound request with this idempotency key is already in progress or has an unresolved outcome.",
            status_code=409,
            code="idempotency_in_progress",
        )

    @staticmethod
    def _accepted_result(
        *,
        message: CanonicalOutboundMessage,
        connection: ResolvedOutboundConnection,
        provider_message_id: str | None,
    ) -> dict[str, Any]:
        return {
            "status": "accepted",
            "providerMessageId": provider_message_id,
            "idempotencyKey": message.idempotency_key,
            "trace": message.trace.model_dump(by_alias=True, mode="json"),
            "provider": connection.provider,
        }

    @staticmethod
    def _log_dispatch_failed(
        *,
        connection: ResolvedOutboundConnection,
        message: CanonicalOutboundMessage,
        correlation_id: str | None,
        status_code: int | None,
    ) -> None:
        logger.warning(
            "canonical_outbound_dispatch_failed",
            connection_id=connection.connection_id,
            channel_id=str(message.channel.id),
            provider=connection.provider,
            channel_type=connection.channel_type,
            idempotency_key=message.idempotency_key,
            correlation_id=correlation_id,
            status_code=status_code,
        )


def _recipient_for_transport(transport: tuple[str, str], external_id: str) -> str:
    mappers: dict[tuple[str, str], Callable[[str], str]] = {
        _EVOLUTION_WHATSAPP: _evolution_number,
        _META_INSTAGRAM: _opaque_external_id,
    }
    recipient = mappers[transport](external_id)
    if recipient:
        return recipient
    if transport == _EVOLUTION_WHATSAPP:
        raise CanonicalOutboundError("WhatsApp recipient externalId is invalid.", status_code=422, code="recipient_invalid")
    raise CanonicalOutboundError("Instagram recipient externalId is invalid.", status_code=422, code="recipient_invalid")


def _validate_message_for_transport(transport: tuple[str, str], message: CanonicalOutboundMessage) -> Any | None:
    validators: dict[tuple[str, str], Callable[[CanonicalOutboundMessage], Any | None]] = {
        _EVOLUTION_WHATSAPP: _validate_whatsapp_message,
        _META_INSTAGRAM: _validate_instagram_text_message,
    }
    return validators[transport](message)


def _validate_whatsapp_message(message: CanonicalOutboundMessage) -> Any | None:
    kind = message.message.kind
    attachment = message.message.attachments[0] if message.message.attachments else None
    if kind != "text" and (kind not in {"image", "video", "audio", "document"} or attachment is None or not (attachment.url or "").strip()):
        raise CanonicalOutboundError("Canonical media delivery requires an attachment URL.", status_code=422, code="message_kind_unsupported")
    return attachment


def _validate_instagram_text_message(message: CanonicalOutboundMessage) -> None:
    if message.message.kind != "text" or message.message.attachments:
        raise CanonicalOutboundError("Canonical Instagram outbound currently supports text messages only.", status_code=422, code="message_kind_unsupported")
    return None


def _evolution_number(external_id: str) -> str:
    """Map the canonical WhatsApp identity to Evolution's number field here only."""
    return "".join(character for character in str(external_id or "") if character.isdigit())


def _opaque_external_id(external_id: str) -> str:
    """Preserve an Instagram recipient exactly; it is not a phone or JID."""
    # ``externalId`` is an opaque Core-owned identifier.  Whitespace-only
    # values are invalid, but otherwise it must arrive at Meta byte-for-byte
    # as Core supplied it (no trim, number conversion, or JID normalization).
    value = str(external_id or "")
    return value if value.strip() else ""
