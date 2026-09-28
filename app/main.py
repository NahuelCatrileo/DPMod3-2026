"""Punto de entrada del Módulo 3 — Publicación y Programación (Equipo C)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.errors import register_error_handlers
from app.api.routes_publish import router as publish_router
from app.config import get_settings
from app.infra.publishers.factory import get_publisher
from app.oauth.routes_oauth import router as oauth_router
from app.scheduler.scheduler import shutdown_scheduler, start_scheduler

settings = get_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "modulo_iniciando publisher_mode=%s event_transport=%s",
        settings.PUBLISHER_MODE,
        settings.EVENT_TRANSPORT,
    )
    get_publisher()
    start_scheduler()
    yield
    shutdown_scheduler()
    logger.info("modulo_detenido")


app = FastAPI(
    title="PubTube · Módulo 3 — Publicación y Programación",
    description=(
        "Equipo C. Scheduler de publicaciones e integración con YouTube "
        "Data API v3 (modo simulado conmutable)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)

register_error_handlers(app)
app.include_router(publish_router)
app.include_router(oauth_router)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    from app.scheduler.scheduler import get_scheduler

    scheduler = get_scheduler()
    return {
        "status": "ok",
        "data": {
            "module": settings.MODULE_NAME,
            "publisherMode": settings.PUBLISHER_MODE,
            "eventTransport": settings.EVENT_TRANSPORT,
            "schedulerRunning": bool(scheduler and scheduler.running),
            "pendingJobs": len(scheduler.get_jobs()) if scheduler else 0,
        },
    }
