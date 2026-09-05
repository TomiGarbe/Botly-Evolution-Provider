"""Provider-neutral outbound delivery owned by Botly Gateway."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.connections import get_connection_manager
from app.core.logging import get_logger
from app.models.canonical_outbound import CanonicalOutboundMessage
from app.platforms.meta import MetaPlatformError
from app.providers.whatsapp_official import get_official_whatsapp_provider
from app.services.connection_registry import ConnectionRegistry, get_connection_registry
from app.services.outbound_provider_attempts import execute_outbound_attempt, get_outbound_provider_attempt_store


logger = get_logger(__name__)


class CanonicalOutboundError(Exception):
    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ResolvedOutboundConnection:
    runtime_name: str
    provider: str
    channel_type: str


class CanonicalOutboundService:
    def __init__(self, *, registry: ConnectionRegistry | None = None, connection_manager: Any | None = None) -> None:
        self._registry = registry or get_connection_registry()
        self._connection_manager = connection_manager or get_connection_manager()

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
        if not runtime_name or provider != message.channel.provider or channel_type != message.channel.channel_type:
            raise CanonicalOutboundError("Canonical transport does not match the resolved Gateway connection.", status_code=409, code="transport_mismatch")
        return ResolvedOutboundConnection(runtime_name=runtime_name, provider=provider, channel_type=channel_type)

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
        logger.info(
            "canonical_outbound_connection_resolved",
            channel_id=str(message.channel.id),
            runtime_name=connection.runtime_name,
            provider=connection.provider,
            channel_type=connection.channel_type,
            correlation_id=message.trace.correlation_id,
        )
        if connection.channel_type != "whatsapp":
            # The endpoint stays generic; providers are enabled deliberately,
            # rather than treating an Instagram id as a WhatsApp number.
            raise CanonicalOutboundError("No canonical outbound adapter is enabled for this channel.", status_code=501, code="provider_not_implemented")

        number = _evolution_number(message.recipient.external_id)
        if not number:
            raise CanonicalOutboundError("WhatsApp recipient externalId is invalid.", status_code=422, code="recipient_invalid")
        kind = message.message.kind
        attachment = message.message.attachments[0] if message.message.attachments else None
        if kind != "text" and (kind not in {"image", "video", "audio", "document"} or attachment is None or not (attachment.url or "").strip()):
            raise CanonicalOutboundError("Canonical media delivery requires an attachment URL.", status_code=422, code="message_kind_unsupported")

        correlation_id = message.trace.correlation_id or message.trace.request_id
        attempt_store = get_outbound_provider_attempt_store()
        attempt = attempt_store.create(
            instance=connection.runtime_name,
            provider=connection.provider,
            message_type=kind,
            recipient=number,
            text=message.message.content,
            correlation_id=correlation_id,
            request_id=message.trace.request_id,
            provider_operation="canonical.outbound.sendText" if kind == "text" else "canonical.outbound.sendMedia",
            media={
                "kind": kind,
                "mimeType": attachment.mime_type if attachment else None,
                "fileName": attachment.filename if attachment else None,
                "source": "canonical_attachment",
            } if attachment else None,
            idempotency_key=message.idempotency_key,
        )
        try:
            if connection.provider == "evolution":
                logger.info("canonical_outbound_provider_send_requested", provider="evolution", runtime_name=connection.runtime_name, correlation_id=correlation_id)
                result, finalized = await execute_outbound_attempt(
                    attempt=attempt,
                    sender=lambda: self._send_evolution(
                        runtime_name=connection.runtime_name,
                        number=number,
                        message=message,
                        attachment=attachment,
                    ),
                    store=attempt_store,
                )
            elif connection.provider == "meta":
                logger.info("canonical_outbound_provider_send_requested", provider="meta", runtime_name=connection.runtime_name, correlation_id=correlation_id)
                result, finalized = await execute_outbound_attempt(
                    attempt=attempt,
                    sender=lambda: self._send_meta(
                        runtime_name=connection.runtime_name,
                        number=number,
                        message=message,
                        attachment=attachment,
                    ),
                    store=attempt_store,
                )
            else:
                raise CanonicalOutboundError("No outbound adapter is enabled for this provider.", status_code=501, code="provider_not_implemented")
        except CanonicalOutboundError:
            raise
        except MetaPlatformError as exc:
            raise CanonicalOutboundError("Provider rejected canonical outbound delivery.", status_code=exc.status_code, code="provider_delivery_failed") from exc
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            raise CanonicalOutboundError("Provider delivery failed.", status_code=status_code if isinstance(status_code, int) else 502, code="provider_delivery_failed") from exc

        provider_message_id = finalized.get("providerMessageId")
        logger.info(
            "canonical_outbound_provider_accepted",
            provider=connection.provider,
            runtime_name=connection.runtime_name,
            provider_message_id=provider_message_id,
            correlation_id=correlation_id,
        )
        return {
            "status": "accepted",
            "providerMessageId": provider_message_id,
            "idempotencyKey": message.idempotency_key,
            "trace": message.trace.model_dump(by_alias=True, mode="json"),
            "provider": connection.provider,
        }

    async def _send_evolution(self, *, runtime_name: str, number: str, message: CanonicalOutboundMessage, attachment: Any | None) -> dict[str, Any]:
        if message.message.kind == "text":
            return await self._connection_manager.send_text(runtime_name, number, message.message.content or "")
        assert attachment is not None
        return await self._connection_manager.send_media(
            runtime_name,
            number,
            attachment.url or "",
            message.message.kind,
            attachment.mime_type or "application/octet-stream",
            attachment.filename or "file.bin",
            message.message.content or "",
        )

    async def _send_meta(self, *, runtime_name: str, number: str, message: CanonicalOutboundMessage, attachment: Any | None) -> dict[str, Any]:
        provider = get_official_whatsapp_provider()
        if message.message.kind == "text":
            return await provider.send_text(instance_name=runtime_name, number=number, text=message.message.content or "")
        assert attachment is not None
        return await provider.send_media(
            instance_name=runtime_name,
            number=number,
            media_base64=attachment.url or "",
            media_type=message.message.kind,
            mime_type=attachment.mime_type or "application/octet-stream",
            file_name=attachment.filename or "file.bin",
            caption=message.message.content or "",
        )


def _evolution_number(external_id: str) -> str:
    """Map the canonical WhatsApp identity to Evolution's number field here only."""
    return "".join(character for character in str(external_id or "") if character.isdigit())
