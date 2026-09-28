"""US-C4 · Subtarea 4.2 — Publicador simulado.

El fallo es DETERMINISTA, no aleatorio: se deriva de un hash del contentId.
Con random.random() un test pasa o falla según el día, y una demo puede
romperse delante del docente sin que nadie sepa por qué. Con hash, el mismo
contentId produce siempre el mismo resultado, y podemos demostrar el camino
de error a voluntad usando un contentId conocido.
"""

from __future__ import annotations

import hashlib
import logging
import time

from app.config import get_settings
from app.domain.errors import ErrorCode
from app.infra.publishers.base import Publisher, PublishResult

logger = logging.getLogger(__name__)


def _bucket(content_id: str) -> float:
    """Mapea un contentId a un valor estable en [0, 1)."""
    digest = hashlib.sha256(content_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


class MockPublisher(Publisher):
    """Simula la publicación en YouTube sin tocar la red ni gastar cuota."""

    name = "mock"

    def __init__(
        self,
        latency_seconds: float | None = None,
        failure_rate: float | None = None,
    ) -> None:
        settings = get_settings()
        self.latency_seconds = (
            settings.MOCK_LATENCY_SECONDS if latency_seconds is None else latency_seconds
        )
        self.failure_rate = (
            settings.MOCK_FAILURE_RATE if failure_rate is None else failure_rate
        )

    def publish_video(self, content_id: str) -> PublishResult:
        logger.info("mock_publish_inicio content_id=%s", content_id)

        if self.latency_seconds > 0:
            time.sleep(self.latency_seconds)

        if self.failure_rate > 0 and _bucket(content_id) < self.failure_rate:
            logger.warning("mock_publish_fallo content_id=%s", content_id)
            return PublishResult.failure(
                ErrorCode.QUOTA_EXCEEDED,
                "Cuota de YouTube agotada (simulada por el publicador mock)",
            )

        video_id = self.fake_video_id(content_id)
        logger.info(
            "mock_publish_ok content_id=%s youtube_video_id=%s", content_id, video_id
        )
        return PublishResult.ok(video_id)

    @staticmethod
    def fake_video_id(content_id: str) -> str:
        """ID de 11 caracteres, igual que los IDs reales de YouTube."""
        digest = hashlib.sha256(f"yt:{content_id}".encode()).hexdigest()
        return digest[:11]
