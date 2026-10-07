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


# Motivos por defecto del fallo simulado, por si el código no está en la
# lista se usa un genérico igual de claro en los logs y en publish.failed.
_MOTIVOS: dict[ErrorCode, str] = {
    ErrorCode.QUOTA_EXCEEDED: "Cuota de YouTube agotada (simulada por el publicador mock)",
}


class MockPublisher(Publisher):
    """Simula la publicación en YouTube sin tocar la red ni gastar cuota."""

    name = "mock"

    def __init__(
        self,
        latency_seconds: float | None = None,
        failure_rate: float | None = None,
        error_code: ErrorCode | None = None,
        failure_attempts: int | None = None,
    ) -> None:
        settings = get_settings()
        self.latency_seconds = (
            settings.MOCK_LATENCY_SECONDS if latency_seconds is None else latency_seconds
        )
        self.failure_rate = (
            settings.MOCK_FAILURE_RATE if failure_rate is None else failure_rate
        )
        # US-C5: qué error devuelve el fallo simulado. Con MOCK_FAILURE_CODE
        # se ensayan los tres caminos en la demo: QUOTA_EXCEEDED (diferible),
        # PUBLISH_FAILED (transitorio) y OAUTH_ERROR (definitivo).
        self.error_code = settings.MOCK_FAILURE_CODE if error_code is None else error_code
        # US-C5: cuántas veces falla cada contentId antes de publicar.
        # 0 = sin límite (falla siempre); con N, el fallo se cura solo y el
        # reintento publica. El contador es por instancia: get_publisher()
        # devuelve un singleton, así que sobrevive entre reintentos.
        self.failure_attempts = (
            settings.MOCK_FAILURE_ATTEMPTS if failure_attempts is None else failure_attempts
        )
        self._fallos: dict[str, int] = {}

    def publish_video(self, content_id: str) -> PublishResult:
        logger.info("mock_publish_inicio content_id=%s", content_id)

        if self.latency_seconds > 0:
            time.sleep(self.latency_seconds)

        if self.failure_rate > 0 and _bucket(content_id) < self.failure_rate:
            fallos = self._fallos.get(content_id, 0)
            if self.failure_attempts > 0 and fallos >= self.failure_attempts:
                logger.info(
                    "mock_publish_se_curo content_id=%s fallos_previos=%s",
                    content_id,
                    fallos,
                )
            else:
                self._fallos[content_id] = fallos + 1
                logger.warning(
                    "mock_publish_fallo content_id=%s error_code=%s fallo_n=%s",
                    content_id,
                    self.error_code.value,
                    self._fallos[content_id],
                )
                return PublishResult.failure(
                    self.error_code,
                    _MOTIVOS.get(
                        self.error_code,
                        f"Fallo simulado por el publicador mock ({self.error_code.value})",
                    ),
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
