"""US-C2 · Scheduler de publicaciones (APScheduler).

  2.1 job store persistente      -> build_scheduler (SQLAlchemyJobStore)
  2.2 registro y cancelación     -> schedule_job / cancel_job, y
                                    services.scheduling.cancel_publication
  2.3 handler                    -> services/publishing.py
  2.4 recuperación tras reinicio -> misfire_grace_time + coalesce, más la
                                    reconciliación de services/recovery.py
                                    al arrancar y cada SCHEDULER_RECONCILE_SECONDS
  2.5/2.6 pruebas                -> tests/test_scheduler_*.py
Fuera de alcance: la política de reintentos (US-C5).

Sobre el job store: APScheduler serializa la referencia a la función por su
ruta de módulo, así que 'app.services.publishing:execute_publication' debe
existir igual tras el reinicio. Si esa ruta cambia, los jobs guardados dejan
de resolverse.

El job de reconciliación vive en un job store en memoria ('internal'): se crea
en cada arranque y no debe contarse como publicación pendiente.

Limitación conocida (va al ADR): con job store compartido y varias réplicas
del contenedor, el mismo job se dispara en todas. Corremos una sola instancia.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings

logger = logging.getLogger(__name__)

JOB_PREFIX = "publish:"
# Ruta textual para que el job sobreviva al reinicio del proceso.
JOB_TARGET = "app.services.publishing:execute_publication"
RECONCILE_JOB_ID = "scheduler:reconcile"
INTERNAL_JOBSTORE = "internal"

_scheduler: BackgroundScheduler | None = None


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    jobstore = SQLAlchemyJobStore(
        url=settings.DATABASE_URL, tablename="apscheduler_jobs"
    )
    return BackgroundScheduler(
        jobstores={"default": jobstore, INTERNAL_JOBSTORE: MemoryJobStore()},
        timezone=settings.SCHEDULER_TIMEZONE,
        job_defaults={
            # Si el contenedor estuvo caído, ejecuta igual dentro del margen.
            "misfire_grace_time": settings.SCHEDULER_MISFIRE_GRACE_SECONDS,
            # Varias ejecuciones atrasadas del mismo job se colapsan en una.
            "coalesce": True,
            "max_instances": 1,
        },
    )


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.SCHEDULER_ENABLED:
        logger.info("scheduler_deshabilitado SCHEDULER_ENABLED=false")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = build_scheduler()
    # Arranca en pausa: primero se reconcilia la tabla con el job store y
    # recién después se empiezan a disparar jobs.
    _scheduler.start(paused=True)
    pendientes = len(publication_jobs())
    logger.info("scheduler_iniciado jobs_recuperados=%s", pendientes)

    from app.services.recovery import reconcile_publications

    reconcile_publications(startup=True)
    _scheduler.add_job(
        reconcile_publications,
        trigger=IntervalTrigger(seconds=settings.SCHEDULER_RECONCILE_SECONDS),
        id=RECONCILE_JOB_ID,
        jobstore=INTERNAL_JOBSTORE,
        replace_existing=True,
    )
    _scheduler.resume()
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler_detenido")
    _scheduler = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def publication_jobs() -> list:
    """Jobs de publicación (sin el job interno de reconciliación)."""
    if _scheduler is None:
        return []
    return _scheduler.get_jobs(jobstore="default")


def job_id_for(publish_id: str) -> str:
    return f"{JOB_PREFIX}{publish_id}"


def schedule_job(publish_id: str, run_at_utc: datetime) -> str | None:
    """Subtarea 2.2 — registra el disparo. replace_existing evita duplicados."""
    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning(
            "job_no_registrado publish_id=%s: el scheduler no está activo", publish_id
        )
        return None

    job_id = job_id_for(publish_id)
    scheduler.add_job(
        JOB_TARGET,
        trigger=DateTrigger(run_date=run_at_utc),
        args=[publish_id],
        id=job_id,
        replace_existing=True,
    )
    logger.info("job_registrado job_id=%s run_at=%s", job_id, run_at_utc.isoformat())
    return job_id


def cancel_job(publish_id: str) -> bool:
    """Quita el job del store. El cambio de estado a 'cancelled' lo hace
    services.scheduling.cancel_publication, que es quien debe llamarse. No se
    emite evento: no hay uno de cancelación en el catálogo (requiere §5.4)."""
    scheduler = get_scheduler()
    if scheduler is None:
        return False
    job_id = job_id_for(publish_id)
    if scheduler.get_job(job_id) is None:
        return False
    scheduler.remove_job(job_id)
    logger.info("job_cancelado job_id=%s", job_id)
    return True
