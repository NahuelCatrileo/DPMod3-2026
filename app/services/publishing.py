"""US-C2 · Subtarea 2.3 — Ejecución de la publicación.

Este es el ÚNICO lugar donde se emiten publish.completed y publish.failed,
sin importar si detrás hay un MockPublisher o un YouTubePublisher. Eso es lo
que hace verdadero el criterio "el mock emite los mismos eventos que el real".

Idempotencia: la transición pending -> publishing se hace con un UPDATE
condicional. Si dos disparos ocurren a la vez (reinicio a destiempo, doble
registro del job), solo uno actualiza filas y el otro se retira sin publicar.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domain.errors import ErrorCode
from app.domain.states import PublishState
from app.infra.db import SessionLocal
from app.infra.events import (
    build_envelope,
    payload_publish_completed,
    payload_publish_failed,
)
from app.infra.events.publisher import get_event_publisher
from app.infra.models import Publication
from app.infra.publishers.factory import get_publisher

logger = logging.getLogger(__name__)


def _claim(session: Session, publish_id: str) -> bool:
    """Intenta tomar la publicación: pending -> publishing.

    Devuelve True solo si esta ejecución ganó la carrera.
    """
    result = session.execute(
        update(Publication)
        .where(
            Publication.id == publish_id,
            Publication.state == PublishState.PENDING.value,
        )
        .values(
            state=PublishState.PUBLISHING.value,
            attempts=Publication.attempts + 1,
            updated_at=datetime.now(dt_timezone.utc),
        )
    )
    session.commit()
    return result.rowcount == 1


def execute_publication(publish_id: str) -> None:
    """Función que dispara el scheduler. Abre su propia sesión a propósito:
    corre en un hilo del scheduler, no en el request de FastAPI."""
    session = SessionLocal()
    try:
        publication = session.get(Publication, publish_id)
        if publication is None:
            logger.error("job_publicacion_inexistente publish_id=%s", publish_id)
            return

        if not _claim(session, publish_id):
            logger.warning(
                "job_ignorado publish_id=%s estado_actual=%s "
                "(ya fue tomado por otra ejecución)",
                publish_id,
                publication.state,
            )
            return

        session.refresh(publication)
        correlation_id = publication.correlation_id
        content_id = publication.content_id
        attempt = publication.attempts

        logger.info(
            "job_publicacion_inicio publish_id=%s content_id=%s intento=%s "
            "correlationId=%s",
            publish_id,
            content_id,
            attempt,
            correlation_id,
        )

        publisher = get_publisher()
        try:
            result = publisher.publish_video(content_id)
        except Exception as exc:  # falla no prevista del publicador
            logger.exception("job_publicacion_excepcion publish_id=%s", publish_id)
            _mark_failed(
                session,
                publication,
                error_code=ErrorCode.PUBLISH_FAILED,
                reason=f"Excepción no controlada: {exc}",
                attempt=attempt,
                correlation_id=correlation_id,
            )
            return

        if result.success and result.youtube_video_id:
            _mark_published(
                session,
                publication,
                youtube_video_id=result.youtube_video_id,
                correlation_id=correlation_id,
            )
        else:
            _mark_failed(
                session,
                publication,
                error_code=result.error_code or ErrorCode.PUBLISH_FAILED,
                reason=result.reason or "Fallo sin detalle",
                attempt=attempt,
                correlation_id=correlation_id,
            )
    finally:
        session.close()


def _mark_published(
    session: Session,
    publication: Publication,
    *,
    youtube_video_id: str,
    correlation_id: str,
) -> None:
    published_at = datetime.now(dt_timezone.utc)
    publication.state = PublishState.PUBLISHED.value
    publication.youtube_video_id = youtube_video_id
    publication.last_error = None
    publication.updated_at = published_at
    session.commit()

    logger.info(
        "publicacion_completada publish_id=%s youtube_video_id=%s correlationId=%s",
        publication.id,
        youtube_video_id,
        correlation_id,
    )

    get_event_publisher().publish(
        build_envelope(
            event_type="publish.completed",
            payload=payload_publish_completed(
                content_id=publication.content_id,
                youtube_video_id=youtube_video_id,
                published_at_iso=published_at.isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                ),
            ),
            correlation_id=correlation_id,
        )
    )


def _mark_failed(
    session: Session,
    publication: Publication,
    *,
    error_code: ErrorCode,
    reason: str,
    attempt: int,
    correlation_id: str,
) -> None:
    publication.state = PublishState.FAILED.value
    publication.last_error = f"{error_code.value}: {reason}"
    publication.updated_at = datetime.now(dt_timezone.utc)
    session.commit()

    logger.warning(
        "publicacion_fallida publish_id=%s error_code=%s correlationId=%s",
        publication.id,
        error_code.value,
        correlation_id,
    )

    get_event_publisher().publish(
        build_envelope(
            event_type="publish.failed",
            payload=payload_publish_failed(
                content_id=publication.content_id,
                error_code=error_code.value,
                attempt=attempt,
                reason=reason,
            ),
            correlation_id=correlation_id,
        )
    )
