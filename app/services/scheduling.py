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

from app.domain.errors import InvalidScheduleDataError, ScheduleConflictError
from app.domain.states import ACTIVE_STATES, PublishState
from app.infra.events import build_envelope, payload_publish_scheduled
from app.infra.events.publisher import get_event_publisher
from app.infra.models import Publication

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
