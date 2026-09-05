import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.core.logging import setup_logging, get_logger
from app.middleware.auth import AuthMiddleware
from app.middleware.cors_diagnostics import CorsDiagnosticsMiddleware
from app.middleware.logging import RequestLoggingMiddleware
from app.routers import alerts, analytics, auth, automations, canonical_outbound, channels, clients, connections, connection_setups, dashboard, instances, messages, operations, provider_deliveries, webhooks, webhook_center, media, instance_webhooks, meta_signup, meta_webhook, provisioning, settings as settings_router
from app.connections import get_connection_manager
from app.services.instances_contract import normalize_instance_list
from app.services.automations import AutomationScheduler
from app.services.operations import OperationWorker
from app.services.connections import get_connection_service
from app.services.connection_setups import get_connection_setup_service
from app.services.connection_setup_cleanup import ConnectionSetupCleanupWorker
from app.services.core_inbound_dispatcher import CoreInboundDeliveryWorker
from app.services.instance_webhooks import protect_stored_webhook_secrets
from app.services.users import bootstrap_initial_admin, bootstrap_meta_reviewer, ensure_meta_review_business

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)
_connection_manager = get_connection_manager()
_connection_service = get_connection_service()
_connection_setup_service = get_connection_setup_service()
_connection_setup_cleanup_worker = ConnectionSetupCleanupWorker()
_core_inbound_delivery_worker = CoreInboundDeliveryWorker()


async def _automation_instances() -> list[dict]:
    raw = await _connection_manager.list_instances()
    return normalize_instance_list(raw if isinstance(raw, list) else [])


_automation_scheduler = AutomationScheduler(_automation_instances)
_operation_worker = OperationWorker(_automation_instances)

# Rate limiter - usa IP como clave por defecto
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])


async def _startup_recovery() -> None:
    logger.info("[BOOT][INIT] startup recovery begin")
    try:
        fetched = await _connection_manager.list_instances()
    except Exception as exc:
        logger.warning("startup_recovery_failed_fetch_instances", error=str(exc))
        return

    instances_list = normalize_instance_list(fetched)
    logger.info("startup_instances_loaded", count=len(instances_list))

    # An open WhatsApp socket does not prove that Evolution retained its callback.
    # Verify every runtime in the background so startup remains non-blocking.
    reconnect_candidates = 0
    for item in instances_list:
        name = item["name"]
        state = item["status"]

        if state != "open":
            reconnect_candidates += 1
        asyncio.create_task(instances._configure_webhook_if_needed(name))

    logger.info("startup_recovery_summary", reconnect_candidates=reconnect_candidates)
    logger.info("[BOOT][SESSIONS] recovery completed", instances_loaded=len(instances_list), reconnect_candidates=reconnect_candidates)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("gateway_starting", port=settings.gateway_port, debug=settings.debug)
    logger.info(
        "cors_config_loaded",
        allowed_origins=settings.cors_allowed_origins_list,
        allow_origin_regex=settings.cors_allow_origin_regex,
        debug=settings.cors_debug,
    )
    logger.info("[BOOT][DB] evolution connectivity setup", evolution_url=settings.evolution_url)
    logger.info(
        "[BOOT][VOLUMES] gateway persistence paths",
        instance_api_keys_path=settings.instance_api_keys_path,
        instance_webhooks_path=settings.instance_webhooks_path,
        connection_metadata_path=settings.connection_metadata_path,
        connection_registry_path=settings.connection_registry_path,
        official_credentials_path=settings.official_credentials_path,
        webhook_events_path=settings.webhook_events_path,
        automations_path=settings.automations_path,
        operations_path=settings.operations_path,
        core_inbound_deliveries_path=settings.core_inbound_deliveries_path,
        media_cache_dir=settings.media_cache_dir,
    )
    bootstrap_initial_admin()
    ensure_meta_review_business()
    bootstrap_meta_reviewer()
    migrated_connections = await _connection_service.migrate_legacy_connections()
    logger.info("connection_domain_migration_complete", inserted=migrated_connections, storage_path=settings.connection_registry_path)
    expired_setups = _connection_setup_service.expire_active()
    logger.info("connection_setup_expiration_complete", expired=expired_setups)
    protected_webhooks = protect_stored_webhook_secrets()
    logger.info("instance_webhook_secret_protection_complete", migrated=protected_webhooks, storage_path=settings.instance_webhooks_path)
    logger.info("[BOOT][WEBHOOKS] instance webhook registry bootstrap", storage_path=settings.instance_webhooks_path)
    await _connection_manager.open_default()
    try:
        await _startup_recovery()
    except Exception as exc:
        logger.critical("startup_recovery_crashed", error=str(exc), exc_info=settings.debug)
        raise
    _automation_scheduler.start()
    _operation_worker.start()
    _connection_setup_cleanup_worker.start()
    _core_inbound_delivery_worker.start()
    yield
    await _core_inbound_delivery_worker.stop()
    await _connection_setup_cleanup_worker.stop()
    await _operation_worker.stop()
    await _automation_scheduler.stop()
    await webhooks.shutdown_forward_workers()
    await _connection_manager.close_default()
    logger.info("gateway_stopped")


api = FastAPI(
    title="Botly Gateway",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

api.state.limiter = limiter
api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

api.add_middleware(RequestLoggingMiddleware)
api.add_middleware(AuthMiddleware)
api.add_middleware(SlowAPIMiddleware)

api.include_router(instances.router)
api.include_router(auth.router)
api.include_router(clients.router)
api.include_router(connections.router)
api.include_router(connection_setups.router)
api.include_router(dashboard.router)
api.include_router(channels.router)
api.include_router(settings_router.router)
api.include_router(provisioning.router)
api.include_router(messages.router)
api.include_router(canonical_outbound.router)
api.include_router(provider_deliveries.router)
api.include_router(analytics.router)
api.include_router(webhooks.router)
api.include_router(meta_webhook.router)
api.include_router(webhook_center.router)
api.include_router(media.router)
api.include_router(instance_webhooks.router)
api.include_router(meta_signup.router)
api.include_router(alerts.router)
api.include_router(automations.router)
api.include_router(operations.router)


@api.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "service": "evolution-gateway",
        "version": settings.gateway_build_version,
        "gitSha": settings.gateway_git_sha,
    }


@api.get("/ready", tags=["system"])
async def ready():
    try:
        data = await _connection_manager.list_instances()
        count = len(data) if isinstance(data, list) else 0
        return {"status": "ready", "service": "evolution-gateway", "instances": count}
    except Exception as exc:
        logger.warning("readiness_failed", error=str(exc))
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "evolution-gateway", "detail": str(exc)},
        )


@api.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.error(
        "unhandled_exception",
        path=str(request.url.path),
        error=str(exc),
        exc_info=settings.debug,
    )
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})


app = CORSMiddleware(
    api,
    allow_origins=settings.cors_allowed_origins_list,
    allow_origin_regex=settings.cors_allow_origin_regex or None,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
    max_age=600,
)
app = CorsDiagnosticsMiddleware(app)
