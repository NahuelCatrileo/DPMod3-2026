"""US-C2 · Subtarea 2.4 — Recuperación tras reinicio (reconciliación).

APScheduler recupera los jobs que siguen en el job store, pero hay tres casos
que el job store solo no resuelve y que dejaban la publicación detenida para
siempre y sin ningún evento:

  1. Vencida: el proceso estuvo caído más que SCHEDULER_MISFIRE_GRACE_SECONDS.
     APScheduler descarta el job en silencio y la fila queda en 'pending'.
  2. Interrumpida: el proceso murió a mitad de la publicación. La fila queda
     en 'publishing' y ningún job la vuelve a tomar.
  3. Sin job: la fila se guardó pero el job no llegó a registrarse (scheduler
     detenido, caída entre el commit y el add_job).

La tabla publication es la fuente de verdad. Esta función deja los jobs
coherentes con ella: reprograma lo que falta y marca 'failed' (emitiendo
publish.failed) lo que ya no se puede ejecutar a tiempo. Decisión en ADR-0004.

US-C5 (ADR-0006) cambia dos ramas:

  - "Vencida" solo aplica a publicaciones NUNCA intentadas (attempts == 0).
    Una fila pending con attempts > 0 no está perdida: está esperando su
    reintento, y su hora no es schedule_at sino updated_at + backoff.
  - "Interrumpida" se trata como un fallo TRANSITORIO: si quedan intentos
    vuelve a pending con su job de reintento; si no, failed como antes.

Las transiciones usan el mismo UPDATE condicional que el job handler, así que
si una ejecución real y la reconciliación compiten, solo una gana.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.errors import ErrorClass, ErrorCode, classify
from app.domain.states import PublishState
from app.infra.db import SessionLocal
from app.infra.events import build_envelope, payload_publish_failed
from app.infra.events.publisher import get_event_publisher
from app.infra.models import Publication
from app.services.retry import retry_delay_seconds, should_retry

logger = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    # Filas a las que se les (re)creó el job: pendientes sin job y
    # publicaciones interrumpidas que volvieron a pending (US-C5).
    reprogramadas: list[str] = field(default_factory=list)
    # Filas nunca intentadas que quedaron fuera de la ventana de misfire.
    vencidas: list[str] = field(default_factory=list)
    # Filas huérfanas en 'publishing' detectadas al arrancar, tanto si
    # volvieron a pending como si terminaron en failed.
    interrumpidas: list[str] = field(default_factory=list)


def reconcile_publications(
    *, now: datetime | None = None, startup: bool = False
) -> ReconcileReport:
    """Reconciliación entre la tabla publication y el job store.

    `now` se puede fijar para que las pruebas sean deterministas.
    `startup=True` solo se usa al arrancar: en ese momento ningún job de este
    proceso está corriendo, así que toda fila en 'publishing' quedó huérfana
    (corremos una sola instancia, ADR-0003).
    """
    # Import local para evitar un ciclo entre services y scheduler.
    from app.scheduler.scheduler import get_scheduler, job_id_for, schedule_job

    settings = get_settings()
    now = now or datetime.now(dt_timezone.utc)
    grace = timedelta(seconds=settings.SCHEDULER_MISFIRE_GRACE_SECONDS)
    scheduler = get_scheduler()
    report = ReconcileReport()

    session = SessionLocal()
    try:
        pendientes = (
            session.execute(
                select(Publication).where(Publication.state == PublishState.PENDING.value)
            )
            .scalars()
            .all()
        )
        for pub in pendientes:
            # US-C5: solo una publicación NUNCA intentada puede estar
            # "vencida". Con attempts > 0 la fila está esperando su
            # reintento, cuya hora es updated_at + backoff (abajo).
            if pub.attempts == 0 and pub.schedule_at + grace < now:
                atraso = int((now - pub.schedule_at).total_seconds())
                reason = (
                    f"Publicación vencida: el scheduler no la disparó a tiempo "
                    f"(atraso de {atraso} s, margen de {int(grace.total_seconds())} s)"
                )
                if _fail_if_state(session, pub, PublishState.PENDING, reason):
                    report.vencidas.append(pub.id)
                    if scheduler is not None and scheduler.get_job(job_id_for(pub.id)):
                        scheduler.remove_job(job_id_for(pub.id))
                continue

            if scheduler is not None and scheduler.get_job(job_id_for(pub.id)) is None:
                schedule_job(pub.id, _proxima_ejecucion(pub, now))
                report.reprogramadas.append(pub.id)
                logger.warning("job_reprogramado publish_id=%s (no estaba en el job store)", pub.id)

        if startup:
            en_curso = (
                session.execute(
                    select(Publication).where(Publication.state == PublishState.PUBLISHING.value)
                )
                .scalars()
                .all()
            )
            for pub in en_curso:
                reason = "Publicación interrumpida: el proceso se detuvo durante la publicación"
                report.interrumpidas.append(pub.id)
                # US-C5: una interrupción es un fallo transitorio (no sabemos
                # qué salió mal, solo que el proceso murió). Si quedan
                # intentos, vuelve a pending con su reintento; si no, failed.
                if should_retry(ErrorClass.TRANSITORIO, pub.attempts):
                    if _requeue_if_state(session, pub, reason=reason, now=now):
                        report.reprogramadas.append(pub.id)
                else:
                    _fail_if_state(session, pub, PublishState.PUBLISHING, reason)
    finally:
        session.close()

    if report.reprogramadas or report.vencidas or report.interrumpidas:
        logger.info(
            "reconciliacion reprogramadas=%s vencidas=%s interrumpidas=%s",
            len(report.reprogramadas),
            len(report.vencidas),
            len(report.interrumpidas),
        )
    return report


def _proxima_ejecucion(pub: Publication, now: datetime) -> datetime:
    """Hora a la que debe dispararse un job que hay que (re)crear.

    - attempts == 0: su hora original de programación (o ya, si está
      atrasada dentro del margen). Es el caso de ADR-0004 sin cambios.
    - attempts > 0: está esperando un reintento (US-C5). Su hora se calcula
      desde el último cambio de la fila (updated_at) más el delay de la
      clase del último error, así el backoff sobrevive a la caída del
      proceso que lo iba a disparar.
    """
    if pub.attempts == 0:
        return max(pub.schedule_at, now)
    delay = retry_delay_seconds(_clase_del_ultimo_error(pub), pub.attempts)
    return max(pub.updated_at + timedelta(seconds=delay), now)


def _clase_del_ultimo_error(pub: Publication) -> ErrorClass:
    """Lee la clase del error guardado en `last_error` (formato "CODE: motivo").

    Es el mismo campo que exponen GET /status y publish.failed; si no hay
    error registrado, se asume transitorio (es lo que corresponde cuando no
    se sabe qué pasó).
    """
    if not pub.last_error:
        return ErrorClass.TRANSITORIO
    codigo, _, _ = pub.last_error.partition(":")
    try:
        return classify(ErrorCode(codigo.strip()))
    except ValueError:
        return ErrorClass.TRANSITORIO


def _requeue_if_state(
    session: Session, pub: Publication, *, reason: str, now: datetime
) -> bool:
    """US-C5 · publishing -> pending con UPDATE condicional + job de reintento.

    Mismo esquema que _fail_if_state: solo pasa si la fila seguía en
    'publishing', así una carrera con el job handler no duplica jobs.
    """
    # Import local para evitar un ciclo entre services y scheduler.
    from app.scheduler.scheduler import schedule_job

    error_code = ErrorCode.PUBLISH_FAILED
    delay = retry_delay_seconds(ErrorClass.TRANSITORIO, pub.attempts)
    run_at = now + timedelta(seconds=delay)
    result = session.execute(
        update(Publication)
        .where(Publication.id == pub.id, Publication.state == PublishState.PUBLISHING.value)
        .values(
            state=PublishState.PENDING.value,
            last_error=f"{error_code.value}: {reason}",
            updated_at=now,
        )
    )
    session.commit()
    if result.rowcount != 1:
        return False

    schedule_job(pub.id, run_at)
    logger.warning(
        "publicacion_interrumpida_reencolada publish_id=%s intento=%s "
        "delay_s=%s proximo_intento=%s correlationId=%s",
        pub.id,
        pub.attempts,
        delay,
        run_at.isoformat(),
        pub.correlation_id,
    )
    return True


def _fail_if_state(session, pub: Publication, expected: PublishState, reason: str) -> bool:
    """origin -> failed con UPDATE condicional. Emite publish.failed solo si ganó."""
    error_code = ErrorCode.PUBLISH_FAILED
    result = session.execute(
        update(Publication)
        .where(Publication.id == pub.id, Publication.state == expected.value)
        .values(
            state=PublishState.FAILED.value,
            last_error=f"{error_code.value}: {reason}",
            updated_at=datetime.now(dt_timezone.utc),
        )
    )
    session.commit()
    if result.rowcount != 1:
        return False

    logger.warning(
        "publicacion_fallida publish_id=%s origen=%s error_code=%s correlationId=%s",
        pub.id,
        expected.value,
        error_code.value,
        pub.correlation_id,
    )
    get_event_publisher().publish(
        build_envelope(
            event_type="publish.failed",
            payload=payload_publish_failed(
                content_id=pub.content_id,
                error_code=error_code.value,
                attempt=pub.attempts,
                reason=reason,
            ),
            correlation_id=pub.correlation_id,
        )
    )
    return True
