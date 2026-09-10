from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.connections import ConnectionManager, get_connection_manager
from app.domain.connection import (
    Channel,
    Connection,
    ConnectionApiKey,
    ConnectionCapabilities,
    ConnectionClient,
    ConnectionStatus,
    ConnectionWebhook,
    Provider,
)
from app.services.connection_adapter import legacy_instance_to_connection
from app.services.connection_registry import ConnectionRegistry, get_connection_registry
from app.services.gateway_settings import (
    ChannelDisabledError,  # noqa: F401 - re-exported for the connections router API
    ChannelNotImplementedError,  # noqa: F401 - re-exported for the connections router API
    GatewaySettingsService,
    ProviderDisabledError,  # noqa: F401 - re-exported for the connections router API
    ProviderNotImplementedError,  # noqa: F401 - re-exported for the connections router API
    get_gateway_settings_service,
)
from app.services.instances_contract import normalize_instance_list
from app.services.evolution_webhook import ensure_evolution_webhook
from app.services.credential_manager import CredentialManager, ProviderAccountReference, get_credential_manager
from app.services.core_channel_credentials import CoreChannelCredentialStore, get_core_channel_credential_store
from app.providers.instagram import MetaInstagramProvider
from app.domain.models import ChannelId, ChannelStatus, MethodId, ProvisionedChannel, RuntimeId
from app.services.normalization import save_event


_MIGRATION_CLIENT_ID = str(uuid5(NAMESPACE_URL, "botly-gateway:migrated-connections"))
_WHATSAPP_CHANNEL = "whatsapp"
_INSTAGRAM_CHANNEL = "instagram"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ConnectionNotFoundError(KeyError):
    pass


class ConnectionClientNotFoundError(KeyError):
    pass


class UnsupportedConnectionChannelError(ValueError):
    pass


class UnsupportedConnectionProviderError(ValueError):
    pass


class ConnectionService:
    """Product-domain connection service with the provider runtime behind an adapter."""

    def __init__(
        self,
        connection_manager: ConnectionManager | None = None,
        registry: ConnectionRegistry | None = None,
        gateway_settings: GatewaySettingsService | None = None,
        credentials: CredentialManager | None = None,
        core_channel_credentials: CoreChannelCredentialStore | None = None,
    ) -> None:
        self._connection_manager = connection_manager or get_connection_manager()
        self._registry = registry or get_connection_registry()
        self._gateway_settings = gateway_settings or get_gateway_settings_service()
        self._credentials = credentials or get_credential_manager()
        self._core_channel_credentials = core_channel_credentials or get_core_channel_credential_store()

    def _migration_client_record(self) -> dict[str, str | None]:
        record = self._registry.get_client(_MIGRATION_CLIENT_ID)
        if record:
            return record
        now = _now()
        return {
            "id": _MIGRATION_CLIENT_ID,
            "name": "Migrated connections",
            "description": "Automatically assigned during the Connection domain migration.",
            "created_at": now,
            "updated_at": now,
        }

    async def migrate_legacy_connections(self) -> int:
        raw = await self._connection_manager.list_instances()
        normalized = normalize_instance_list(raw if isinstance(raw, list) else [])
        return self._registry.ensure_legacy_connections(normalized, self._migration_client_record())

    @staticmethod
    def _runtime_name(record: dict[str, Any]) -> str:
        return str(record.get("legacy_name") or "")

    def _client_reference(self, client_id: str) -> ConnectionClient:
        client = self._registry.get_client(client_id)
        if client is None:
            raise ConnectionClientNotFoundError(client_id)
        return ConnectionClient(id=str(client["id"]), name=str(client["name"]))

    def _stored_connection(self, record: dict[str, Any]) -> Connection:
        client = self._client_reference(str(record["client_id"]))
        is_instagram = str(record.get("channel_id") or "") == _INSTAGRAM_CHANNEL
        provider_account = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else None
        core_channel_data = record.get("core_channel") if isinstance(record.get("core_channel"), dict) else None
        core_channel = None
        if core_channel_data and str(core_channel_data.get("channelId") or "").strip():
            core_channel = {
                "channelId": str(core_channel_data["channelId"]),
                "name": str(core_channel_data.get("name") or "") or None,
                "configured": True,
            }
        readiness = self.instagram_readiness(str(record["id"])) if is_instagram else None
        return Connection(
            id=str(record["id"]),
            client_id=client.id,
            name=str(record.get("name") or "WhatsApp"),
            display_name=str(record["display_name"]) if record.get("display_name") else None,
            address=str(record["address"]) if record.get("address") else None,
            provider=Provider(
                id=str(record.get("provider_id") or "meta"),
                display_name=str(record.get("provider_display_name") or "Meta"),
            ),
            channel=Channel(
                id=str(record.get("channel_id") or _WHATSAPP_CHANNEL),
                display_name=str(record.get("channel_display_name") or "WhatsApp"),
            ),
            status=ConnectionStatus(
                state=str(record.get("status_state") or "pending"),
                lifecycle=str(record["status_lifecycle"]) if record.get("status_lifecycle") else None,
                health=str(record.get("status_health") or "unknown"),
            ),
            capabilities=ConnectionCapabilities(
                supports_messaging=True,
                # G3 can receive and verify Meta Instagram callbacks. Message
                # delivery to Core remains deliberately unavailable until G4.
                supports_webhook=True,
                supports_media=not is_instagram,
                supports_qr=str(record.get("provider_id") or "meta") == "evolution",
                supports_reconnect=True,
                # Instagram exposes the same connection-scoped outbound API as
                # the other runtimes once its OAuth readiness is satisfied.
                supports_api_key=True,
                supports_official_api=str(record.get("provider_id") or "meta") == "meta",
            ),
            webhook=ConnectionWebhook(supported=True),
            api_key=ConnectionApiKey(supported=True),
            client=client,
            last_activity_at=str(record["last_activity_at"]) if record.get("last_activity_at") else None,
            created_at=str(record["created_at"]) if record.get("created_at") else None,
            updated_at=str(record["updated_at"]) if record.get("updated_at") else None,
            provider_account=provider_account,
            core_channel=core_channel,
            readiness=readiness,
            technical={"legacy_instance_name": str(record.get("legacy_name") or "") or None},
        )

    async def _runtime_connections(self) -> dict[str, dict[str, Any]]:
        try:
            raw = await self._connection_manager.list_instances()
        except Exception:
            return {}
        normalized = normalize_instance_list(raw if isinstance(raw, list) else [])
        self._registry.ensure_legacy_connections(normalized, self._migration_client_record())
        return {str(item["name"]): item for item in normalized}

    async def list_connections(self, client_id: str | None = None) -> list[Connection]:
        runtime_connections = await self._runtime_connections()
        result: list[Connection] = []
        for record in self._registry.connection_records():
            if client_id is not None and str(record.get("client_id")) != client_id:
                continue
            runtime = runtime_connections.get(self._runtime_name(record))
            try:
                if runtime is not None:
                    client = self._client_reference(str(record["client_id"]))
                    result.append(
                        replace(
                            legacy_instance_to_connection(runtime, record, client.name),
                            technical={"legacy_instance_name": self._runtime_name(record)},
                        )
                    )
                else:
                    result.append(self._stored_connection(record))
            except ConnectionClientNotFoundError:
                continue
        return sorted(result, key=lambda connection: (connection.name.lower(), connection.id))

    async def get_connection(self, connection_id: str) -> Connection:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        for connection in await self.list_connections(str(record["client_id"])):
            if connection.id == connection_id:
                return connection
        raise ConnectionNotFoundError(connection_id)

    def create_connection(self, *, client_id: str, channel: str, name: str | None = None, provider: str = "meta") -> Connection:
        client = self._client_reference(client_id)
        self._gateway_settings.require_channel_available(channel)
        if channel not in {_WHATSAPP_CHANNEL, _INSTAGRAM_CHANNEL}:
            raise UnsupportedConnectionChannelError(f"Unsupported connection channel: {channel}")
        if provider not in {"meta", "evolution"}:
            raise UnsupportedConnectionProviderError(f"Unsupported provider: {provider}")
        if channel == _INSTAGRAM_CHANNEL and provider != "meta":
            raise UnsupportedConnectionProviderError("Instagram connections require provider meta")
        self._gateway_settings.require_provider_available(provider)
        # Meta (WhatsApp Cloud) es independiente del motor Evolution: opera directo
        # contra la Graph API. No exigir el proveedor "evolution" para conexiones Meta.
        clean_name = str(name or "").strip()
        if not clean_name:
            clean_name = "Instagram" if channel == _INSTAGRAM_CHANNEL else "WhatsApp Oficial" if provider == "meta" else "WhatsApp Evolution"
        now = _now()
        connection_id = str(uuid4())
        record = {
            "id": connection_id,
            "legacy_name": f"connection_{connection_id.replace('-', '')[:24]}",
            "client_id": client.id,
            "name": clean_name,
            "provider_id": provider,
            "provider_display_name": "Meta" if provider == "meta" else "Evolution",
            "channel_id": channel,
            "channel_display_name": "Instagram" if channel == _INSTAGRAM_CHANNEL else "WhatsApp",
            "status_state": "oauth_pending" if channel == _INSTAGRAM_CHANNEL else "pending" if provider == "meta" else "connecting",
            "status_health": "unknown",
            "created_at": now,
            "updated_at": now,
        }
        saved = self._registry.save_connection_record_for_client(str(record["legacy_name"]), record)
        if saved is None:
            raise ConnectionClientNotFoundError(client.id)
        return self._stored_connection(record)

    def require_instagram_meta_connection(self, connection_id: str) -> dict[str, Any]:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        if str(record.get("provider_id") or "") != "meta" or str(record.get("channel_id") or "") != _INSTAGRAM_CHANNEL:
            raise UnsupportedConnectionProviderError("Instagram OAuth requires a Meta + Instagram connection")
        return record

    def instagram_readiness(self, connection_id: str, *, required_scopes: tuple[str, ...] = ()) -> dict[str, Any]:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            return {"state": "no_connection", "ready": False}
        self.require_instagram_meta_connection(connection_id)
        if str(record.get("status_state") or "") == "disconnected":
            return {"state": "disconnected", "ready": False}
        account_data = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else None
        if not account_data:
            return {"state": "oauth_pending", "ready": False, "configured": True, "authenticated": False}
        account = ProviderAccountReference(
            provider_id="meta",
            channel_type="instagram",
            provider_account_id=str(account_data.get("providerAccountId") or ""),
        )
        credential = self._credentials.get_provider_credentials(account)
        if credential is None:
            return {"state": "credential_missing", "ready": False, "configured": True, "authenticated": True, "accountDiscovered": True}
        expiry = credential.expiry_state()
        if expiry == "expired":
            return {"state": "expired", "ready": False, "configured": True, "authenticated": True, "accountDiscovered": True, "credentialValid": False, "tokenExpiry": expiry}
        missing = sorted(set(required_scopes) - set(credential.scopes))
        if missing:
            return {"state": "missing_scopes", "ready": False, "configured": True, "authenticated": True, "accountDiscovered": True, "credentialValid": True, "missingScopes": missing, "tokenExpiry": expiry}
        core_binding = self.instagram_core_channel_binding(connection_id)
        core_credential_valid = False
        if core_binding:
            try:
                core_credential_valid = bool(self._core_channel_credentials.get_api_key(
                    connection_id=connection_id, core_channel_id=core_binding["channelId"]
                ))
            except Exception:
                core_credential_valid = False
        return {
            "state": "ready", "ready": True, "configured": True, "authenticated": True,
            "accountDiscovered": True, "credentialValid": True, "requiredScopesPresent": True,
            "tokenExpiry": expiry, "coreBindingPresent": bool(core_binding),
            "coreCredentialValid": core_credential_valid,
            "coreDeliveryReady": bool(core_binding) and core_credential_valid,
        }

    def bind_instagram_provider_account(
        self,
        *,
        connection_id: str,
        account: ProviderAccountReference,
        metadata: dict[str, Any],
        required_scopes: tuple[str, ...],
    ) -> Connection:
        self.require_instagram_meta_connection(connection_id)
        if account.provider_id != "meta" or account.channel_type != "instagram":
            raise UnsupportedConnectionProviderError("Invalid provider account for Instagram connection")
        self.assert_instagram_provider_account_available(connection_id, account)
        readiness = self.instagram_readiness(connection_id, required_scopes=required_scopes)
        if readiness["state"] in {"credential_missing", "expired", "missing_scopes"}:
            raise UnsupportedConnectionProviderError("Instagram credentials do not satisfy connection readiness")
        updated = self._registry.update_connection_record(
            connection_id,
            {
                "provider_account": {
                    "provider": account.provider_id,
                    "channelType": account.channel_type,
                    "providerAccountId": account.provider_account_id,
                    "metadata": dict(metadata),
                },
                "status_state": "connected",
                "status_health": "healthy",
                "updated_at": _now(),
            },
        )
        if updated is None:
            raise ConnectionNotFoundError(connection_id)
        return self._stored_connection(updated)

    def mark_instagram_core_delivery_pending(self, connection_id: str) -> Connection:
        """Keep a completed OAuth connection out of inbound service until Core binds."""
        self.require_instagram_meta_connection(connection_id)
        updated = self._registry.update_connection_record(
            connection_id,
            {"status_state": "connecting", "status_health": "unknown", "updated_at": _now()},
        )
        if updated is None:
            raise ConnectionNotFoundError(connection_id)
        return self._stored_connection(updated)

    def assert_instagram_provider_account_available(self, connection_id: str, account: ProviderAccountReference) -> None:
        self.require_instagram_meta_connection(connection_id)
        for candidate in self._registry.connection_records():
            binding = candidate.get("provider_account") if isinstance(candidate.get("provider_account"), dict) else {}
            if str(binding.get("providerAccountId") or "") == account.provider_account_id and str(candidate.get("id") or "") != connection_id:
                raise UnsupportedConnectionProviderError("Instagram provider account is already bound to another connection")

    def resolve_active_instagram_provider_account(self, provider_account_id: str) -> Connection:
        """Resolve a webhook account ID through persisted Gateway bindings only."""
        target = str(provider_account_id or "")
        matches: list[dict[str, Any]] = []
        for record in self._registry.connection_records():
            binding = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else {}
            if str(binding.get("providerAccountId") or "") != target:
                continue
            if str(record.get("provider_id") or "") != "meta" or str(record.get("channel_id") or "") != _INSTAGRAM_CHANNEL:
                raise UnsupportedConnectionProviderError("Instagram provider account has an invalid connection binding")
            matches.append(record)
        if not matches:
            raise ConnectionNotFoundError(provider_account_id)
        if len(matches) != 1:
            raise UnsupportedConnectionProviderError("Instagram provider account has ambiguous connection bindings")
        record = matches[0]
        if str(record.get("status_state") or "") != "connected":
            raise UnsupportedConnectionProviderError("Instagram connection is not active")
        return self._stored_connection(record)

    async def send_instagram_text(self, *, connection_id: str, external_id: str, text: str, provider: MetaInstagramProvider | None = None) -> dict[str, Any]:
        """Send text through the provider adapter; externalId is never normalized as a phone."""
        record = self.require_instagram_meta_connection(connection_id)
        # Instagram contact IDs are opaque Core identities.  Do not trim or
        # normalize them as phone numbers/JIDs; only reject an empty value.
        recipient = str(external_id or "")
        content = str(text or "").strip()
        if not recipient.strip():
            raise ValueError("Instagram recipient externalId is required")
        if not content:
            raise ValueError("Instagram text is required")
        if not self.instagram_readiness(connection_id).get("ready"):
            raise UnsupportedConnectionProviderError("Instagram connection is not ready for messaging")
        binding = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else {}
        account_id = str(binding.get("providerAccountId") or "").strip()
        account = ProviderAccountReference("meta", "instagram", account_id)
        token = self._credentials.get_provider_access_token(account)
        if not token:
            raise UnsupportedConnectionProviderError("Instagram credentials are unavailable")
        account_metadata = binding.get("metadata") if isinstance(binding.get("metadata"), dict) else {}
        # A connection may explicitly provide a Graph send node distinct from
        # its account identity.  Existing Instagram Login connections use the
        # professional account as both identities, so retain that compatible
        # fallback until the connection model stores a separate node.
        graph_send_node_id = str(account_metadata.get("graphSendNodeId") or account_id).strip()
        if not graph_send_node_id:
            raise UnsupportedConnectionProviderError("Instagram Graph send node is unavailable")
        channel = ProvisionedChannel(
            id=f"connection:{connection_id}", channel_id=ChannelId.INSTAGRAM, method_id=MethodId.OFFICIAL,
            integration_id="instagram.official.meta", runtime_id=RuntimeId.META, display_name=str(record.get("name") or "Instagram"),
            status=ChannelStatus.ACTIVE, metadata={"graphSendNodeId": graph_send_node_id},
        )
        canonical = {"channel": {"channelType": "instagram"}, "recipient": {"externalId": recipient}, "message": {"type": "text", "text": content}}
        result = await (provider or MetaInstagramProvider()).send_text(channel=channel, recipient_id=recipient, text=content, access_token=token)
        message_id = str(result.get("message_id") or result.get("messageId") or "") or None
        save_event({
            "id": str(uuid4()), "event": "LOCAL_OUTBOUND_SEND", "layer": "business", "instance": connection_id,
            "timestamp": int(time.time() * 1000), "type": "message", "subtype": "text", "messageType": "text", "direction": "outbound",
            "text": content, "content": {"text": content}, "sender": account_id, "recipient": recipient,
            "message": {"id": message_id, "from": account_id, "fromMe": True, "kind": "text", "text": content}, "provider": "meta",
            "providerDelivery": {"provider": "meta", "providerMessageId": message_id, "connectionId": connection_id, "channelId": "instagram"},
            "meta": {"connectionId": connection_id, "channelId": "instagram", "providerAccountId": account_id}, "canonical": canonical,
        })
        self._registry.update_connection_record(connection_id, {"last_activity_at": _now(), "updated_at": _now()})
        return {"ok": True, "providerMessageId": message_id, "recipient": {"externalId": recipient}}

    def bind_instagram_core_channel(
        self,
        *,
        connection_id: str,
        core_channel_id: str,
        dispatch_credential: str,
        core_binding_id: str,
        core_channel_name: str | None = None,
    ) -> Connection:
        """Bind one Core Channel credential to one Meta Instagram connection."""
        self.require_instagram_meta_connection(connection_id)
        clean_channel_id = str(core_channel_id or "").strip()
        if not clean_channel_id or not str(dispatch_credential or "").strip() or not str(core_binding_id or "").strip():
            raise UnsupportedConnectionProviderError("Core channel binding response is incomplete")
        credential_ref = self._core_channel_credentials.upsert(
            connection_id=connection_id,
            core_channel_id=clean_channel_id,
            channel_api_key=dispatch_credential,
        )
        updated = self._registry.update_connection_record(
            connection_id,
            {
                "core_channel": {
                    "channelId": clean_channel_id,
                    "credentialRef": credential_ref,
                    "bindingId": str(core_binding_id),
                    "name": str(core_channel_name or "").strip() or None,
                },
                "status_state": "connected",
                "status_health": "healthy",
                "updated_at": _now(),
            },
        )
        if updated is None:
            self._core_channel_credentials.delete(connection_id)
            raise ConnectionNotFoundError(connection_id)
        return self._stored_connection(updated)

    def instagram_core_channel_binding(self, connection_id: str) -> dict[str, str] | None:
        record = self.require_instagram_meta_connection(connection_id)
        binding = record.get("core_channel") if isinstance(record.get("core_channel"), dict) else None
        channel_id = str((binding or {}).get("channelId") or "").strip()
        if not channel_id:
            return None
        return {
            "channelId": channel_id,
            "credentialRef": str(binding.get("credentialRef") or ""),
            "bindingId": str(binding.get("bindingId") or ""),
        }

    def disconnect_instagram_connection(self, connection_id: str) -> Connection:
        record = self.require_instagram_meta_connection(connection_id)
        binding = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else None
        if binding:
            self._credentials.delete_provider_credentials(
                ProviderAccountReference("meta", "instagram", str(binding.get("providerAccountId") or ""))
            )
        self._core_channel_credentials.delete(connection_id)
        updated = self._registry.update_connection_record(
            connection_id,
            {"provider_account": None, "core_channel": None, "status_state": "disconnected", "status_health": "unknown", "updated_at": _now()},
        )
        if updated is None:
            raise ConnectionNotFoundError(connection_id)
        return self._stored_connection(updated)

    async def get_connection_by_runtime_name(self, instance_name: str) -> Connection:
        """Resolve a legacy runtime name through the product ownership registry."""
        for connection in await self.list_connections():
            if self._runtime_name(self._registry.connection_record_by_id(connection.id) or {}) == instance_name:
                return connection
        raise ConnectionNotFoundError(instance_name)

    async def start_evolution_connection(self, connection_id: str) -> Connection:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        if str(record.get("provider_id") or "") != "evolution":
            raise UnsupportedConnectionProviderError("This connection does not use Evolution")
        self._gateway_settings.require_provider_available("evolution")
        try:
            await self._connection_manager.create(
                self._runtime_name(record),
                qrcode=True,
                connection_type="baileys",
            )
        except Exception:
            self._registry.delete_connection_record(connection_id)
            raise
        try:
            await ensure_evolution_webhook(self._connection_manager, self._runtime_name(record), force_configure=True)
        except Exception:
            # Preserve a possibly paired runtime for diagnosis, but do not
            # expose an initial connection as usable without its callback.
            self._registry.update_connection_record(
                connection_id,
                {"status_state": "error", "status_health": "degraded", "updated_at": _now()},
            )
            raise
        return self._stored_connection(record)

    async def evolution_qr(self, connection_id: str) -> dict[str, Any]:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        if str(record.get("provider_id") or "") != "evolution":
            raise UnsupportedConnectionProviderError("This connection does not use Evolution")
        self._gateway_settings.require_provider_available("evolution")
        return await self._connection_manager.connect(self._runtime_name(record), connection_type="baileys")

    async def update_connection(self, connection_id: str, *, name: str | None = None) -> Connection:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        if name is None:
            raise ValueError("At least one connection field must be provided")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Connection name is required")
        updated = self._registry.update_connection_record(
            connection_id,
            {"name": clean_name, "updated_at": _now()},
        )
        if updated is None:
            raise ConnectionNotFoundError(connection_id)
        return await self.get_connection(connection_id)

    async def delete_connection(self, connection_id: str) -> None:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        if str(record.get("channel_id") or "") == _INSTAGRAM_CHANNEL and str(record.get("provider_id") or "") == "meta":
            binding = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else None
            if binding:
                self._credentials.delete_provider_credentials(
                    ProviderAccountReference("meta", "instagram", str(binding.get("providerAccountId") or ""))
                )
            self._core_channel_credentials.delete(connection_id)
        runtime = (await self._runtime_connections()).get(self._runtime_name(record))
        if runtime is not None:
            await self._connection_manager.delete(self._runtime_name(record))
        deleted = self._registry.delete_connection_record(connection_id)
        if deleted is None:
            raise ConnectionNotFoundError(connection_id)

    def connection_runtime_name(self, connection_id: str) -> str:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        return self._runtime_name(record)

    def connection_last_heartbeat_at(self, connection_id: str) -> str | None:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        value = record.get("last_heartbeat_at")
        return str(value) if value else None

    def mark_meta_signup_completed(self, connection_id: str) -> Connection:
        record = self._registry.update_connection_record(
            connection_id,
            {"status_state": "connected", "status_health": "healthy", "updated_at": _now()},
        )
        if record is None:
            raise ConnectionNotFoundError(connection_id)
        return self._stored_connection(record)


def get_connection_service() -> ConnectionService:
    return ConnectionService()
