"""US-C4 · Subtarea 4.3 — Conmutación mock / real por variable de entorno.

Usamos PUBLISHER_MODE=mock|youtube en vez de un booleano USE_MOCK_PUBLISHER:
cuando en Sprint 4 aparezca un tercer modo (por ejemplo 'dry-run' para el
ensayo de la demo integrada) no hay que romper el contrato de configuración.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.infra.publishers.base import Publisher
from app.infra.publishers.mock import MockPublisher
from app.infra.publishers.youtube import YouTubePublisher

logger = logging.getLogger(__name__)

_publisher: Publisher | None = None


def build_publisher(mode: str | None = None) -> Publisher:
    """Construye el publicador según el modo. Sin caché: útil en tests."""
    resolved = mode or get_settings().PUBLISHER_MODE
    if resolved == "mock":
        return MockPublisher()
    if resolved == "youtube":
        return YouTubePublisher()
    raise ValueError(
        f"PUBLISHER_MODE inválido: {resolved!r}. Valores aceptados: 'mock', 'youtube'."
    )


def get_publisher() -> Publisher:
    """Singleton perezoso usado por la aplicación."""
    global _publisher
    if _publisher is None:
        _publisher = build_publisher()
        logger.info("publicador_activo mode=%s", _publisher.name)
    return _publisher


def set_publisher(publisher: Publisher | None) -> None:
    """Punto de inyección para los tests."""
    global _publisher
    _publisher = publisher
