"""US-C3 (Sprint 3) — Publicador real contra YouTube Data API v3.

Esqueleto deliberado. En Sprint 1 esta clase NO se usa: PUBLISHER_MODE=mock.
Está aquí para que la interfaz quede validada por dos implementaciones y para
que en Sprint 3 sólo haya que rellenar el cuerpo, sin tocar el scheduler ni
la emisión de eventos.

NO la marquen como terminada ni la cuenten en el burndown del Sprint 1.
"""

from __future__ import annotations

import logging

from app.domain.errors import ErrorCode
from app.infra.publishers.base import Publisher, PublishResult

logger = logging.getLogger(__name__)


class YouTubePublisher(Publisher):
    name = "youtube"

    def publish_video(self, content_id: str) -> PublishResult:
        # Sprint 3 · US-C3:
        #   1. Cargar credenciales OAuth y refrescarlas si expiraron.
        #   2. Obtener del Módulo 1 los metadatos y la URL del archivo.
        #   3. Llamar a youtube.videos().insert(...) con MediaFileUpload.
        #   4. Mapear errores de la API (US-C5):
        #        403 quotaExceeded        -> QUOTA_EXCEEDED   (transitorio)
        #        401 / invalid_grant      -> OAUTH_ERROR      (definitivo)
        #        5xx / socket timeout     -> PUBLISH_FAILED   (transitorio)
        logger.error("YouTubePublisher todavía no está implementado (US-C3, Sprint 3)")
        return PublishResult.failure(
            ErrorCode.PUBLISH_FAILED,
            "YouTubePublisher no implementado: pendiente de US-C3 (Sprint 3)",
        )
