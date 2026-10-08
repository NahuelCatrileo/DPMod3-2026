"""US-C1 · Subtareas 1.3 y 1.4 — Programar una publicación.

Responsabilidad: validar, persistir, registrar el job y emitir publish.scheduled.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import (
    InvalidScheduleDataError,
    PublicationNotFoundError,
    ScheduleConflictError,
)
from app.domain.states import ACTIVE_STATES, PublishState, supports_transition
from app.infra.events import build_envelope, payload_publish_scheduled
from app.infra.events.publisher import get_event_publisher
from app.infra.models import Publication
from app.services.transitions import transition_conditionally

logger = logging.getLogger(__name__)


def validate_timezone(tz_name: str) -> ZoneInfo:
    """Subtarea 1.3 — la zona horaria debe ser un identificador IANA real."""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise InvalidScheduleDataError(
            f"Zona horaria desconocida: {tz_name!r}. "
            "Use un identificador IANA, por ejemplo 'America/Santiago'."
        ) from exc


def normalize_schedule_at(schedule_at: datetime, tz_name: str) -> datetime:
    """Devuelve el instante en UTC.

    Si scheduleAt viene sin offset, se interpreta en la zona horaria indicada.
    Si viene con offset, se respeta y `timezone` queda como dato de presentación.
    """
    tz = validate_timezone(tz_name)
    if schedule_at.tzinfo is None:
        schedule_at = schedule_at.replace(tzinfo=tz)
    return schedule_at.astimezone(dt_timezone.utc)


def _assert_future(schedule_at_utc: datetime) -> None:
    now = datetime.now(dt_timezone.utc)
    if schedule_at_utc <= now:
        raise ScheduleConflictError(
            f"scheduleAt debe ser una fecha futura. Recibido {schedule_at_utc.isoformat()}, "
            f"ahora es {now.isoformat()}."
        )


def _assert_no_active_schedule(session: Session, content_id: str) -> None:
    """SCHEDULE_CONFLICT en su sentido literal: ya hay una programación viva."""
    stmt = select(Publication).where(
        Publication.content_id == content_id,
        Publication.state.in_([s.value for s in ACTIVE_STATES]),
    )
    existing = session.execute(stmt).scalars().first()
    if existing is not None:
        raise ScheduleConflictError(
            f"El contenido {content_id} ya tiene la publicación {existing.id} "
            f"en estado '{existing.state}'."
        )


def schedule_publication(
    session: Session,
    *,
    content_id: str,
    schedule_at: datetime,
    tz_name: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> Publication:
    """Valida, persiste y emite publish.scheduled. Devuelve la Publication."""
    schedule_at_utc = normalize_schedule_at(schedule_at, tz_name)
    _assert_future(schedule_at_utc)
    _assert_no_active_schedule(session, content_id)

    publication = Publication(
        id=str(uuid.uuid4()),
        content_id=content_id,
        state=PublishState.PENDING.value,
        schedule_at=schedule_at_utc,
        timezone=tz_name,
        attempts=0,
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    session.add(publication)
    session.commit()
    session.refresh(publication)

    logger.info(
        "publicacion_programada publish_id=%s content_id=%s schedule_at=%s "
        "timezone=%s correlationId=%s",
        publication.id,
        publication.content_id,
        schedule_at_utc.isoformat(),
        tz_name,
        publication.correlation_id,
    )

    # Registro del job (US-C2 · Subtarea 2.2). Import local para evitar un
    # ciclo entre services y scheduler.
    from app.scheduler.scheduler import schedule_job

    schedule_job(publication.id, schedule_at_utc)

    # Subtarea 1.4 — emisión del evento
    envelope = build_envelope(
        event_type="publish.scheduled",
        payload=payload_publish_scheduled(
            content_id=publication.content_id,
            schedule_at_iso=schedule_at_utc.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            tz_name=tz_name,
        ),
        correlation_id=publication.correlation_id,
        causation_id=causation_id,
    )
    get_event_publisher().publish(envelope)

    return publication


def get_publication(session: Session, publish_id: str) -> Publication | None:
    return session.get(Publication, publish_id)


def cancel_publication(session: Session, publish_id: str) -> Publication:
    """US-C2 · Subtarea 2.2 — Cancela una publicación programada.

    Primero cambia el estado (pending -> cancelled) y después quita el job. Si
    el job se disparara entre medio, el handler no podría tomar la publicación
    porque ya no está en 'pending'.

    US-C6: la transición pasa por `transition_conditionally`, así que además
    del UPDATE condicional se valida el par contra la matriz. Es la tercera
    ruta productiva que cambia estados (junto con el job handler y la
    reconciliación) y antes escribía el estado por su cuenta.

    Sin endpoint REST ni evento: ninguno de los dos está en el contrato y
    agregarlos requiere el procedimiento de la Guía §5.4.
    """
    publication = session.get(Publication, publish_id)
    if publication is None:
        raise PublicationNotFoundError(publish_id)

    # Mismo patrón que `recovery._fail_if_state`: primero se comprueba que el
    # par (estado actual, cancelled) esté en la matriz. Así el error de
    # conflicto lo sigue viendo quien llama, con el contrato de siempre, en
    # lugar de un `InvalidTransitionError` que no espera nadie.
    if not supports_transition(PublishState(publication.state), PublishState.CANCELLED):
        raise ScheduleConflictError(
            f"La publicación {publish_id} no se puede cancelar: "
            f"está en estado '{publication.state}'."
        )

    # La carrera (otra ejecución movió la fila entre la comprobación y este
    # UPDATE) la resuelve la condición del UPDATE: `rowcount` 0 y sin escritura.
    if not transition_conditionally(session, publication, PublishState.CANCELLED):
        # `transition_conditionally` ya resincronizó el objeto con la base, así
        # que `publication.state` es el estado real al momento de informar.
        raise ScheduleConflictError(
            f"La publicación {publish_id} no se puede cancelar: "
            f"está en estado '{publication.state}'."
        )

    from app.scheduler.scheduler import cancel_job

    cancel_job(publish_id)
    logger.info(
        "publicacion_cancelada publish_id=%s content_id=%s correlationId=%s",
        publication.id,
        publication.content_id,
        publication.correlation_id,
    )
    return publication
