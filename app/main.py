"""Punto de entrada del Modulo 3 - Publicacion y Programacion (Equipo C)."""

import logging

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="PubTube - Modulo 3 - Publicacion y Programacion",
    version="0.1.0",
)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    return {
        "status": "ok",
        "data": {
            "module": settings.MODULE_NAME,
            "publisherMode": settings.PUBLISHER_MODE,
            "eventTransport": settings.EVENT_TRANSPORT,
        },
    }