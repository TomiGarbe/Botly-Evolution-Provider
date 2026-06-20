from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Gateway
    gateway_api_key: str
    gateway_port: int = 9000
    public_base_url: str = ""
    log_level: str = "info"
    debug: bool = False
    cors_allowed_origins: str = (
        "http://localhost:5174,"
        "http://127.0.0.1:5174,"
        "https://panel-evolution.botly.com.ar"
    )

    # Evolution API
    evolution_url: str = "http://evolution:8080"
    evolution_api_key: str

    # Bot (destino de los webhooks procesados)
    bot_webhook_url: str = ""
    bot_webhook_timeout: int = 5  # segundos
    bot_webhook_retries: int = 3
    bot_webhook_backoff_base_ms: int = 400
    bot_webhook_max_parallel: int = 20
    bot_webhook_max_queue: int = 200

    dedupe_ttl_seconds: int = 180
    dedupe_max_items: int = 20000
    outbound_echo_ttl_seconds: int = 45
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
    instance_webhooks_path: str = "/tmp/botly_instance_webhooks.json"
    instance_webhook_timeout: int = 8
    webhook_debug: bool = False
    webhook_dispatch_history_limit: int = 30
    allow_insecure_evolution_webhooks: bool = False
    evolution_auth_cache_ttl_seconds: int = 45

    model_config = SettingsConfigDict(
        # En Docker las variables llegan por environment: en el compose.
        # env_file solo aplica si corrés el gateway localmente fuera de Docker.
        env_file="../config/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        origins: list[str] = []
        seen: set[str] = set()

        for raw_origin in self.cors_allowed_origins.split(","):
            origin = raw_origin.strip()
            if not origin:
                continue
            if origin.endswith("/"):
                origin = origin.rstrip("/")
            if origin not in seen:
                origins.append(origin)
                seen.add(origin)

        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
