from functools import lru_cache
import re

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Gateway
    gateway_api_key: str
    google_client_id: str = ""
    allowed_google_users: str = ""
    auth_sessions_path: str = "/tmp/botly_gateway_sessions.json"
    auth_session_ttl_seconds: int = 28800
    gateway_users_database_url: str = ""
    initial_admin_email: str = ""
    initial_admin_password: str = ""
    initial_admin_name: str = "Botly Administrator"
    meta_review_email: str = ""
    meta_review_password: str = ""
    meta_review_name: str = "Meta Review"
    # Comma-separated email=role assignments. Roles are evaluated on each request.
    authorization_role_assignments_raw: str = Field(default="", validation_alias="AUTHORIZATION_ROLE_ASSIGNMENTS")
    gateway_port: int = 9000
    gateway_git_sha: str = "unknown"
    gateway_build_version: str = "unknown"
    # PUBLIC_BASE_URL se conserva como alias de lectura para no interrumpir
    # despliegues existentes durante la migracion del dominio.
    public_app_url: str = Field(
        default="",
        validation_alias=AliasChoices("PUBLIC_APP_URL", "PUBLIC_BASE_URL"),
    )
    # Browser UI origin. Keep this separate from PUBLIC_APP_URL, which is the
    # Gateway's canonical external URL and is used by provider webhooks.
    frontend_app_url: str = Field(default="", validation_alias="FRONTEND_APP_URL")
    log_level: str = "info"
    debug: bool = False
    environment: str = "development"
    cors_allowed_origins: str = (
        "https://gateway.botly.com.ar,"
        "http://localhost:5174,"
        "http://127.0.0.1:5174"
    )
    cors_allow_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    cors_debug: bool = False
    feature_provider_evolution: bool = False
    feature_provider_baileys: bool = False
    feature_whatsapp_web: bool = False
    feature_qr_login: bool = False
    feature_instagram: bool = True
    feature_whatsapp_cloud: bool = True

    # Evolution API
    evolution_url: str = "http://evolution:8080"
    evolution_api_key: str
    # Shared secret sent by Evolution as a configured per-instance webhook
    # header.  Keep it distinct from the API key used by Gateway -> Evolution.
    evolution_webhook_secret: str = ""
    # Docker-internal callback URL that Evolution uses to reach this Gateway.
    # Empty preserves the local Compose service-name default.
    evolution_webhook_url: str = ""

    # Bot (destino de los webhooks procesados)
    bot_webhook_url: str = ""
    bot_webhook_timeout: int = 5  # segundos
    bot_webhook_retries: int = 3
    bot_webhook_backoff_base_ms: int = 400
    bot_webhook_max_parallel: int = 20
    bot_webhook_max_queue: int = 200
    enable_group_messages: bool = False

    dedupe_ttl_seconds: int = 180
    dedupe_max_items: int = 20000
    outbound_echo_ttl_seconds: int = 45
    max_event_age_seconds: int = 600
    flood_window_seconds: int = 3
    flood_max_messages: int = 12
    webhook_event_retention: int = 2000

    # Rate limiting (slowapi usa strings "X/minute")
    rate_limit_default: str = "1000/minute"
    rate_limit_send_message: str = "20/second"
    media_max_upload_mb: int = 25
    media_allowed_mime_prefixes: str = "image/,video/,audio/,application/pdf,application/msword,application/vnd.openxmlformats-officedocument"
    media_cache_dir: str = "/tmp/botly_media_cache"
    media_cache_ttl_seconds: int = 3600
    media_cache_max_files: int = 500
    media_download_timeout: int = 30
    instance_api_keys_path: str = "/tmp/botly_instance_api_keys.json"
    # Key used to encrypt instance API keys at rest so an authorized user can
    # reveal a previously generated key from the Workspace. When unset, the
    # Gateway API key is used as backwards-compatible key material.
    instance_api_keys_encryption_key: str = ""
    instance_webhooks_path: str = "/tmp/botly_instance_webhooks.json"
    # Optional dedicated material for encrypting outbound webhook secrets at
    # rest. The Gateway API key remains a backwards-compatible fallback.
    instance_webhooks_encryption_key: str = ""
    connection_metadata_path: str = "/tmp/botly_connection_metadata.json"
    # Product-domain ownership registry. It is additive and intentionally
    # separate from provider/runtime persistence during the Instance migration.
    # Docker mounts /var/lib/botly on a named volume, so this default survives
    # normal container recreation. Non-Docker deployments can override it with
    # CONNECTION_REGISTRY_PATH.
    connection_registry_path: str = "/var/lib/botly/connections/connection_registry.json"
    # Active setup lifetime. Expired records remain for diagnosis and any
    # manual compensation; they are never silently deleted.
    connection_setup_ttl_seconds: int = 3600
    connection_setup_cleanup_interval_seconds: int = 60
    connection_setup_cleanup_batch_size: int = 25
    official_credentials_path: str = "/tmp/botly_official_credentials.json"
    # Timeline business events shown by /webhooks/events.  It must survive a
    # Gateway restart so a received first message is not lost before the UI
    # reads it.
    webhook_events_path: str = "/tmp/botly_webhook_events.json"
    # Alertas operativas persistentes. Se mantiene separado del timeline para
    # que reconocer o resolver una alerta no modifique la evidencia original.
    alerts_path: str = "/tmp/botly_alerts.json"
    # Definiciones y ejecuciones del motor operativo. El historial se conserva
    # separado de la configuración para que pueda auditarse sin reconstruirlo.
    automations_path: str = "/tmp/botly_automations.json"
    automation_scheduler_enabled: bool = True
    operations_path: str = "/tmp/botly_operations.json"
    operations_worker_enabled: bool = True
    operations_target_concurrency: int = 8
    # Key used to encrypt the long-lived WhatsApp Cloud token at rest.  When it
    # is not explicitly configured, the gateway API key is used as key material
    # so existing installations remain operable; production deployments should
    # provide a distinct, high-entropy value.
    official_credentials_encryption_key: str = ""
    # Dedicated key for provider-account OAuth credentials. Production must not
    # fall back to GATEWAY_API_KEY when storing these long-lived tokens.
    provider_credentials_encryption_key: str = ""
    # Channel-scoped credentials used exclusively for Gateway -> Core canonical
    # inbound delivery. They must never share Meta/OAuth credentials.
    core_channel_credentials_path: str = "/var/lib/botly/core/core_channel_credentials.json"
    core_channel_credentials_encryption_key: str = ""
    core_inbound_url: str = ""
    # Dedicated Core control-plane base URL. It is deliberately separate from
    # CORE_INBOUND_URL, which only receives canonical events from G4.
    core_control_plane_url: str = ""
    # Server-to-server credential shared only with Core's narrow Gateway
    # control-plane. It is never a browser or channel credential.
    gateway_control_plane_api_key: str = ""
    core_control_plane_timeout_seconds: int = 10
    core_inbound_deliveries_path: str = "/var/lib/botly/core/inbound_deliveries.json"
    core_inbound_delivery_max_attempts: int = 5
    core_inbound_delivery_backoff_base_seconds: int = 5
    core_inbound_delivery_poll_seconds: int = 2
    core_inbound_delivery_batch_size: int = 25
    core_inbound_delivery_lease_seconds: int = 60
    core_inbound_dispatcher_enabled: bool = True
    instagram_oauth_state_path: str = "/var/lib/botly/oauth/instagram_states.json"
    instagram_oauth_state_ttl_seconds: int = 600
    meta_onboarding_path: str = "/tmp/botly_meta_onboarding.json"
    meta_resources_path: str = "/tmp/botly_meta_resources.json"
    channel_records_path: str = "/tmp/botly_channel_records.json"
    instance_webhook_timeout: int = 8
    webhook_debug: bool = False
    webhook_dispatch_history_limit: int = 30
    # Delivery evidence is deliberately independent from webhook configuration.
    # Existing dispatchHistory data remains readable as legacy compatibility.
    webhook_deliveries_path: str = "/var/lib/botly/webhooks/deliveries.json"
    webhook_delivery_retention: int = 250
    webhook_delivery_max_payload_bytes: int = 16_384
    # Outbound provider attempts are persisted before the provider side effect.
    # This store contains only redacted/reconstructibility metadata, never raw
    # authorization headers or media payloads.
    outbound_provider_attempts_path: str = "/var/lib/botly/provider-deliveries/outbound_attempts.json"
    outbound_provider_attempt_retention: int = 2_000
    allow_insecure_evolution_webhooks: bool = False
    evolution_auth_cache_ttl_seconds: int = 45

    # Meta Embedded Signup
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_embedded_signup_config_id: str = ""
    meta_graph_version: str = "v23.0"
    meta_signup_timeout_seconds: int = 30
    # Webhook oficial de WhatsApp Cloud API. No reutilizar la clave de Evolution:
    # Meta llama al Gateway directamente y firma cada POST con META_APP_SECRET.
    meta_webhook_verify_token: str = ""
    meta_webhook_require_signature: bool = True
    # Instagram API with Instagram Login. This is intentionally separate from
    # META_APP_ID, which remains the app identifier for Meta/WhatsApp flows.
    # Both are validated lazily when an Instagram authorization is started, so
    # WhatsApp/Evolution startup remains usable without Instagram configured.
    instagram_app_id: str = ""
    # Credential issued by Instagram's API setup for Instagram Login. It is
    # intentionally independent from META_APP_SECRET, which remains used by
    # the Meta/WhatsApp integrations and webhook signature verification.
    instagram_app_secret: str = ""
    meta_redirect_uri: str = ""
    instagram_oauth_scopes: str = "instagram_business_basic,instagram_business_manage_messages"
    instagram_oauth_authorize_url: str = "https://www.instagram.com/oauth/authorize"
    instagram_oauth_token_url: str = "https://api.instagram.com/oauth/access_token"
    instagram_graph_api_url: str = "https://graph.instagram.com"
    # Keep Instagram delivery versioning separate from Meta/WhatsApp Graph.
    instagram_graph_api_version: str = "v23.0"

    model_config = SettingsConfigDict(
        # En Docker las variables llegan por environment: en el compose.
        # env_file solo aplica si corrés el gateway localmente fuera de Docker.
        env_file="../config/.env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        origins: list[str] = []
        for item in self.cors_allowed_origins.split(","):
            origin = item.strip().rstrip("/")
            if origin and origin not in origins:
                origins.append(origin)
        return origins

    @property
    def allowed_google_users_list(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_google_users.split(",") if item.strip()}

    @property
    def authorization_role_assignments(self) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for item in self.authorization_role_assignments_raw.split(","):
            email, separator, role = item.partition("=")
            if separator and email.strip() and role.strip():
                assignments[email.strip().lower()] = role.strip().lower()
        return assignments

    def is_cors_origin_allowed(self, origin: str | None) -> bool:
        value = str(origin or "").strip().rstrip("/")
        if not value:
            return False
        if "*" in self.cors_allowed_origins_list:
            return True
        if value in self.cors_allowed_origins_list:
            return True
        if self.cors_allow_origin_regex:
            try:
                return re.fullmatch(self.cors_allow_origin_regex, value) is not None
            except re.error:
                return False
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
