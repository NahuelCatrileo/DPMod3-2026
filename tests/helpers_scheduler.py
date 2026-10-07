"""Utilidades compartidas por las pruebas del scheduler (US-C2). No es un test."""

import time
from datetime import datetime, timedelta, timezone

from app.infra.models import Publication
from app.scheduler import scheduler as sched
from app.services.scheduling import schedule_publication


def stop_scheduler() -> None:
    """Apagado ordenado que espera a los jobs en curso (para no dejar hilos
    escribiendo en una base que el fixture está por borrar)."""
    s = sched.get_scheduler()
    if s is not None and s.running:
        s.shutdown(wait=True)
    sched.shutdown_scheduler()


def esperar_estado(session, publish_id: str, estado: str, limite: float = 10) -> Publication:
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        session.expire_all()
        pub = session.get(Publication, publish_id)
        if pub.state == estado:
            return pub
        time.sleep(0.05)
    raise AssertionError(f"{publish_id} no llegó a '{estado}' (quedó en '{pub.state}')")


def programar(session, content_id: str, segundos: float) -> Publication:
    return schedule_publication(
        session,
        content_id=content_id,
        schedule_at=datetime.now(timezone.utc) + timedelta(seconds=segundos),
        tz_name="America/Santiago",
    )
