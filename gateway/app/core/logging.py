import logging
import sys
from typing import Any

import structlog
from app.core.config import get_settings


def setup_logging(level: str = "info") -> None:
    """Configura structlog con salida JSON estructurada."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    settings = get_settings()
    verbose_debug = bool(settings.webhook_debug)
    # Reduce ruido de transporte HTTP y access logs en modo normal.
    logging.getLogger("httpx").setLevel(logging.INFO if verbose_debug else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.INFO if verbose_debug else logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            # add_logger_name requiere stdlib logger — lo sacamos, el nombre
            # llega como parámetro en get_logger(name) y queda en el contexto
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__) -> Any:
    return structlog.get_logger(name).bind(logger=name)
