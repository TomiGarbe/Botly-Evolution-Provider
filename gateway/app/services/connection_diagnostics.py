from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app.connections import ConnectionManager, get_connection_manager
from app.core.config import get_settings
from app.services.connection_registry import ConnectionRegistry, get_connection_registry
from app.services.credential_manager import CredentialManager, ProviderAccountReference, get_credential_manager
from app.services.instance_webhooks import list_instance_webhooks
from app.services.normalization import list_events


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event_time(event: dict[str, Any]) -> int | None:
    value = event.get("timestamp")
    return int(value) if isinstance(value, (int, float)) else None


class ConnectionDiagnosticsService:
    """Build the operator snapshot from the Gateway's existing sources.

    This service deliberately does not make a second persistence model. Runtime,
    credentials, webhooks and the event timeline remain their respective sources
    of truth; it only translates them into an operator-safe diagnostic view.
    """

    def __init__(
        self,
        connection_manager: ConnectionManager | None = None,
        registry: ConnectionRegistry | None = None,
        credentials: CredentialManager | None = None,
        events_reader: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        self._connection_manager = connection_manager or get_connection_manager()
        self._registry = registry or get_connection_registry()
        self._credentials = credentials or get_credential_manager()
        self._events_reader = events_reader or list_events

    def diagnose(self, instance: dict[str, Any], *, raw: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Compatibility diagnostics used by the legacy instance endpoint."""
        raw = raw or {}
        diagnostics: list[dict[str, Any]] = []
        name = str(instance.get("name") or raw.get("name") or "")
        connection_type = str(instance.get("connectionType") or raw.get("connectionType") or "")
        is_cloud = connection_type == "cloud" or instance.get("integration") == "WHATSAPP-BUSINESS"
        if not is_cloud:
            return diagnostics
        credentials_info = self._credentials.get_official_credentials_info(name) if name else None
        lifecycle_signals = raw.get("lifecycleSignals") if isinstance(raw.get("lifecycleSignals"), dict) else {}
        if not credentials_info and lifecycle_signals.get("tokenConfigured") is not True:
            diagnostics.append({"code": "official_credentials_missing", "severity": "error", "message": "La conexión oficial no tiene credenciales registradas en el Provider.", "action": "Completá Embedded Signup nuevamente o recreá la conexión oficial."})
        if credentials_info and not credentials_info.access_token_hash:
            diagnostics.append({"code": "official_token_hash_missing", "severity": "warning", "message": "La referencia de token existe, pero falta la huella para auditoría.", "action": "Actualizá la credencial mediante el CredentialManager."})
        if lifecycle_signals.get("coexistenceState") == "failed":
            diagnostics.append({"code": "coexistence_failed", "severity": "error", "message": "La conexión con WhatsApp Business App falló o fue deshabilitada.", "action": "Revisá la cuenta desde WhatsApp Business App y repetí el flujo oficial de Meta."})
        return diagnostics

    async def snapshot(self, connection_id: str) -> dict[str, Any]:
        record = self._registry.connection_record_by_id(connection_id)
        if record is None:
            raise KeyError(connection_id)
        if str(record.get("provider_id") or "") == "meta" and str(record.get("channel_id") or "") == "instagram":
            return self._instagram_snapshot(record)
        runtime_name = str(record.get("legacy_name") or "").strip()
        if not runtime_name:
            raise ValueError("La conexión todavía no tiene un runtime disponible.")

        instances = await self._connection_manager.list_instances()
        runtime = next((item for item in instances if isinstance(item, dict) and str(item.get("name") or item.get("instanceName") or "") == runtime_name), None)
        now = _now()
        raw_state = str((runtime or {}).get("status") or (runtime or {}).get("connectionStatus") or "").lower()
        connected = raw_state in {"open", "connected"}
        if runtime is not None:
            self._registry.update_connection_record(connection_id, {"last_heartbeat_at": now, "updated_at": now})
        credential = self._credentials.get_official_credentials_info(runtime_name)
        webhooks = list_instance_webhooks(runtime_name, reveal_secrets=False)
        webhook = webhooks[0] if webhooks else None
        events = self._events_reader(instance=runtime_name, limit=500)
        latest_error = next((event for event in events if self._is_error(event)), None)
        last_sent = next((event for event in events if event.get("direction") == "outbound" and event.get("type") == "message"), None)
        last_received = next((event for event in events if event.get("direction") == "inbound" and event.get("type") == "message"), None)
        connection_type = str((runtime or {}).get("connectionType") or "")
        is_official = connection_type == "cloud" or (runtime or {}).get("integration") == "WHATSAPP-BUSINESS" or credential is not None

        checks = [
            self._check("gateway", "Gateway", "healthy" if runtime is not None and connected else "degraded" if runtime is not None else "unhealthy", now, "La instancia está disponible." if connected else "El Gateway no informa una instancia abierta.", "Usá Reconectar y luego actualizá el diagnóstico." if not connected else None),
            self._check("provider", "Provider", "healthy" if connected else "degraded", now, "El proveedor informa una conexión abierta." if connected else "El proveedor no confirmó una conexión abierta.", "Revisá el estado de la conexión y solicitá una reconexión."),
            self._check("meta_api", "Meta API", "healthy" if credential else ("unknown" if not is_official else "unhealthy"), credential.updated_at if credential else now, "Credencial oficial disponible para Graph API." if credential else ("No aplica para este proveedor." if not is_official else "No hay credenciales oficiales disponibles."), "Completá nuevamente Embedded Signup." if is_official and not credential else None),
            self._check("token", "Token", "healthy" if credential and credential.access_token_hash else ("unknown" if not is_official else "unhealthy"), credential.updated_at if credential else now, "Token cifrado y registrado." if credential and credential.access_token_hash else ("No aplica para este proveedor." if not is_official else "No se encontró un token utilizable."), "Reconectá la cuenta oficial para renovar sus credenciales." if is_official and not credential else None),
            self._check("phone_number", "Phone Number", "healthy" if credential and credential.phone_number_id else ("unknown" if not is_official else "unhealthy"), credential.updated_at if credential else now, "Phone Number ID disponible." if credential and credential.phone_number_id else ("No aplica para este proveedor." if not is_official else "Falta el Phone Number ID."), "Completá Embedded Signup nuevamente." if is_official and not credential else None),
            self._check("business", "Business", "healthy" if credential and credential.business_account_id else ("unknown" if not is_official else "unhealthy"), credential.updated_at if credential else now, "Business y WABA disponibles." if credential and credential.business_account_id else ("No aplica para este proveedor." if not is_official else "Falta la cuenta de negocio."), "Completá Embedded Signup nuevamente." if is_official and not credential else None),
            self._check("webhook", "Webhook", "healthy" if webhook and webhook.get("enabled") else "degraded", str((webhook or {}).get("lastUsedAt") or now), "Webhook activo." if webhook and webhook.get("enabled") else "No hay un webhook activo.", "Configurá o activá el webhook y ejecutá una prueba."),
            self._check("heartbeat", "Heartbeat", "healthy" if runtime is not None else "unknown", now if runtime is not None else str(record.get("last_heartbeat_at") or now), "El runtime respondió a la última verificación." if runtime is not None else "No hubo respuesta del runtime en la última verificación.", "Actualizá el estado o reconectá la conexión." if runtime is None else None),
        ]
        overall = "unhealthy" if any(item["status"] == "unhealthy" for item in checks) else "degraded" if any(item["status"] == "degraded" for item in checks) else "healthy" if any(item["status"] == "healthy" for item in checks) else "unknown"
        return {
            "summary": {
                "status": overall,
                "last_verified_at": now,
                "last_heartbeat_at": now if runtime is not None else record.get("last_heartbeat_at"),
                "last_message_sent_at": _event_time(last_sent) if last_sent else None,
                "last_message_received_at": _event_time(last_received) if last_received else None,
                "last_webhook_success_at": (webhook or {}).get("lastSuccessAt"),
                "last_error": self._error_message(latest_error),
            },
            "checks": checks,
            "technical": {
                "phone_number_id": credential.phone_number_id if credential else None,
                "business_id": credential.business_account_id if credential else None,
                "waba_id": credential.business_account_id if credential else None,
                "provider": "Meta" if is_official else "Evolution",
                "channel": "WhatsApp",
                "api_version": get_settings().meta_graph_version if is_official else None,
                "last_synchronized_at": credential.updated_at if credential else record.get("updated_at"),
            },
        }

    def _instagram_snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        """Diagnose Instagram from its real persisted bindings, not Evolution runtime state."""
        now = _now()
        connection_id = str(record.get("id") or "")
        account_data = record.get("provider_account") if isinstance(record.get("provider_account"), dict) else {}
        account_id = str(account_data.get("providerAccountId") or "").strip()
        credential = None
        if account_id:
            credential = self._credentials.get_provider_credentials(ProviderAccountReference("meta", "instagram", account_id))
        expiry = credential.expiry_state() if credential else "missing"
        required_scopes = {"instagram_business_basic", "instagram_business_manage_messages"}
        missing_scopes = sorted(required_scopes - set(credential.scopes if credential else ()))
        binding = record.get("core_channel") if isinstance(record.get("core_channel"), dict) else {}
        core_channel_id = str(binding.get("channelId") or "").strip()
        webhooks = list_instance_webhooks(str(record.get("legacy_name") or ""), reveal_secrets=False)
        webhook = next((item for item in webhooks if item.get("enabled")), None)
        events = self._events_reader(instance=connection_id, limit=500)
        latest_error = next((event for event in events if self._is_error(event)), None)
        last_sent = next((event for event in events if event.get("direction") == "outbound" and event.get("type") == "message"), None)
        last_received = next((event for event in events if event.get("direction") == "inbound" and event.get("type") == "message"), None)
        settings = get_settings()
        connected = str(record.get("status_state") or "") == "connected"
        checks = [
            self._check("gateway", "Gateway", "healthy" if connected else "degraded", now, "La conexión está registrada en Gateway." if connected else "La cuenta no está conectada en Gateway.", "Reautorizá Instagram para restablecer el vínculo." if not connected else None),
            self._check("provider_account", "Cuenta profesional", "healthy" if account_id else "unhealthy", str(record.get("updated_at") or now), "La cuenta profesional está vinculada." if account_id else "No hay una cuenta profesional vinculada.", "Conectá o reautorizá Instagram." if not account_id else None),
            self._check("oauth", "Credencial OAuth", "healthy" if credential and expiry != "expired" else "unhealthy", credential.updated_at if credential else now, "La credencial OAuth está disponible." if credential and expiry != "expired" else "No hay una credencial OAuth utilizable.", "Reautorizá Instagram con la misma conexión." if not credential or expiry == "expired" else None),
            self._check("scopes", "Scopes", "healthy" if credential and not missing_scopes else "unhealthy", credential.updated_at if credential else now, "Los scopes necesarios están presentes." if credential and not missing_scopes else f"Faltan scopes: {', '.join(missing_scopes)}.", "Reautorizá Instagram y aceptá los permisos solicitados." if missing_scopes else None),
            self._check("meta_webhook", "Webhook Meta", "healthy" if bool(getattr(settings, "instagram_app_secret", "")) else "unhealthy", now, "La firma del webhook de Meta está configurada en Gateway." if bool(getattr(settings, "instagram_app_secret", "")) else "Falta configurar la firma de Instagram en Gateway.", "Configurá INSTAGRAM_APP_SECRET." if not bool(getattr(settings, "instagram_app_secret", "")) else None),
            self._check("core_channel", "Canal de Botly", "healthy" if core_channel_id else "degraded", str(record.get("updated_at") or now), "Hay un canal de Botly vinculado." if core_channel_id else "No hay un canal de Botly vinculado.", "Vinculá un canal de Botly para habilitar el handoff duradero." if not core_channel_id else None),
            self._check("outbound_webhook", "Destino webhook", "healthy" if webhook else "degraded", str((webhook or {}).get("lastUsedAt") or now), "Hay un destino webhook activo." if webhook else "No hay un destino webhook activo.", "Configurá un destino desde la pestaña Webhooks." if not webhook else None),
        ]
        overall = "unhealthy" if any(item["status"] == "unhealthy" for item in checks) else "degraded" if any(item["status"] == "degraded" for item in checks) else "healthy"
        return {
            "summary": {"status": overall, "last_verified_at": now, "last_heartbeat_at": record.get("last_heartbeat_at"), "last_message_sent_at": _event_time(last_sent) if last_sent else None, "last_message_received_at": _event_time(last_received) if last_received else None, "last_webhook_success_at": (webhook or {}).get("lastSuccessAt"), "last_error": self._error_message(latest_error)},
            "checks": checks,
            "technical": {"phone_number_id": None, "business_id": account_id or None, "waba_id": None, "provider": "Meta", "channel": "Instagram", "api_version": getattr(settings, "meta_graph_version", None), "last_synchronized_at": credential.updated_at if credential else record.get("updated_at")},
        }

    async def verify_availability(self, connection_id: str) -> dict[str, Any]:
        """Return an honest, read-only availability diagnostic for a connection."""
        snapshot = await self.snapshot(connection_id)
        record = self._registry.connection_record_by_id(connection_id) or {}
        technical = snapshot.get("technical") if isinstance(snapshot.get("technical"), dict) else {}
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        provider = str(technical.get("provider") or "unknown").lower()
        runtime_check = next(
            (item for item in snapshot.get("checks", []) if isinstance(item, dict) and item.get("code") == "gateway"),
            {},
        )
        return {
            "diagnostic": "verify_availability",
            "provider": provider,
            "available": summary.get("status") in {"healthy", "degraded"},
            "runtime_available": runtime_check.get("status") in {"healthy", "degraded"},
            "last_activity_at": record.get("last_activity_at"),
            "deep_provider_health_checked": provider != "meta",
            "limitation": (
                "Meta Cloud API is stateless: credentials and Gateway runtime were checked, but this diagnostic does not make a Graph API request."
                if provider == "meta"
                else None
            ),
            "diagnostics": snapshot,
        }

    @staticmethod
    def _check(code: str, label: str, status: str, last_verified_at: str | None, message: str, action: str | None = None) -> dict[str, Any]:
        return {"code": code, "label": label, "status": status, "last_verified_at": last_verified_at, "message": message, "action": action}

    @staticmethod
    def _is_error(event: dict[str, Any]) -> bool:
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        return str(event.get("severity") or "").upper() in {"ERROR", "CRITICAL"} or str(event.get("result") or event.get("status") or "").lower() in {"error", "failed"} or bool(details.get("error"))

    @staticmethod
    def _error_message(event: dict[str, Any] | None) -> str | None:
        if event is None:
            return None
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        return str(details.get("error") or event.get("error") or event.get("description") or "Se registró un error operativo.")[:300]


def get_connection_diagnostics_service() -> ConnectionDiagnosticsService:
    return ConnectionDiagnosticsService()
