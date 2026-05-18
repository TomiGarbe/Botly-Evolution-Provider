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
from app.middleware.logging import RequestLoggingMiddleware
from app.routers import instances, messages, webhooks, media, instance_webhooks
from app.services import evolution
from app.services.instances_contract import normalize_instance_list

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)

# Rate limiter - usa IP como clave por defecto
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])


async def _startup_recovery() -> None:
    try:
        fetched = await evolution.fetch_instances()
    except Exception as exc:
        logger.warning("startup_recovery_failed_fetch_instances", error=str(exc))
        return

    instances_list = normalize_instance_list(fetched)
    logger.info("startup_instances_loaded", count=len(instances_list))

    # Recovery pragmatico: rehacer webhooks de instancias no conectadas en background.
    reconnect_candidates = 0
    for item in instances_list:
        name = item["name"]
        state = item["status"]

        if state != "open":
            reconnect_candidates += 1
            asyncio.create_task(instances._configure_webhook_if_needed(name))

    logger.info("startup_recovery_summary", reconnect_candidates=reconnect_candidates)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("gateway_starting", port=settings.gateway_port, debug=settings.debug)
    await evolution.get_client()
    try:
        await _startup_recovery()
    except Exception as exc:
        logger.critical("startup_recovery_crashed", error=str(exc), exc_info=settings.debug)
        raise
    yield
    await webhooks.shutdown_forward_workers()
    await evolution.close_client()
    logger.info("gateway_stopped")


app = FastAPI(
    title="Botly Evolution Gateway",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(instances.router)
app.include_router(messages.router)
app.include_router(webhooks.router)
app.include_router(media.router)
app.include_router(instance_webhooks.router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": "evolution-gateway"}


@app.get("/ready", tags=["system"])
async def ready():
    try:
        data = await evolution.fetch_instances()
        count = len(data) if isinstance(data, list) else 0
        return {"status": "ready", "service": "evolution-gateway", "instances": count}
    except Exception as exc:
        logger.warning("readiness_failed", error=str(exc))
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "evolution-gateway", "detail": str(exc)},
        )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.error(
        "unhandled_exception",
        path=str(request.url.path),
        error=str(exc),
        exc_info=settings.debug,
    )
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})
