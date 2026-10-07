"""US-C2 · TECH-C2.5 — Pruebas de disparo, registro y cancelación de jobs.

A diferencia de test_publishing_job.py, que llama al handler directamente,
aquí corre el scheduler real (APScheduler con el job store en la base de
test). Las esperas son cortas y con límite, para que CI no se cuelgue.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.errors import PublicationNotFoundError, ScheduleConflictError
from app.domain.states import PublishState, can_transition
from app.infra.models import Publication
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.mock import MockPublisher
from app.scheduler.scheduler import (
    INTERNAL_JOBSTORE,
    RECONCILE_JOB_ID,
    get_scheduler,
    job_id_for,
    publication_jobs,
    schedule_job,
)
from app.services.scheduling import cancel_publication
from tests.helpers_scheduler import esperar_estado, programar


class RelojPublisher(MockPublisher):
    """MockPublisher que anota el instante en que fue llamado."""

    def __init__(self) -> None:
        super().__init__(latency_seconds=0, failure_rate=0)
        self.llamadas: list[tuple[str, datetime]] = []

    def publish_video(self, content_id: str):
        self.llamadas.append((content_id, datetime.now(timezone.utc)))
        return super().publish_video(content_id)


@pytest.fixture
def reloj():
    p = RelojPublisher()
    set_publisher(p)
    yield p
    set_publisher(None)


# --- Disparo a la hora programada -------------------------------------------


def test_dispara_a_la_hora_programada_sin_adelantarse(session, events, real_scheduler, reloj):
    real_scheduler()
    pub = programar(session, "c-200", 1.5)

    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)

    assert len(reloj.llamadas) == 1
    _, disparo = reloj.llamadas[0]
    assert disparo >= pub.schedule_at, "el job se disparó antes de su hora"
    assert disparo - pub.schedule_at < timedelta(seconds=2)
    assert len(events.events_of_type("publish.completed")) == 1


def test_no_dispara_antes_de_tiempo(session, events, real_scheduler, reloj):
    real_scheduler()
    pub = programar(session, "c-201", 30)

    time.sleep(1)

    session.expire_all()
    assert session.get(Publication, pub.id).state == PublishState.PENDING.value
    assert reloj.llamadas == []


def test_varias_publicaciones_disparan_cada_una_a_su_hora(session, events, real_scheduler, reloj):
    real_scheduler()
    primera = programar(session, "c-202", 2.0)
    segunda = programar(session, "c-203", 1.0)

    esperar_estado(session, primera.id, PublishState.PUBLISHED.value)
    esperar_estado(session, segunda.id, PublishState.PUBLISHED.value)

    assert [c for c, _ in reloj.llamadas] == ["c-203", "c-202"]


def test_el_job_sale_del_store_despues_de_disparar(session, events, real_scheduler, reloj):
    real_scheduler()
    pub = programar(session, "c-204", 1.0)
    assert get_scheduler().get_job(job_id_for(pub.id)) is not None

    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)

    assert get_scheduler().get_job(job_id_for(pub.id)) is None


# --- Registro de jobs ---------------------------------------------------------


def test_programar_registra_el_job_con_su_hora(session, events, real_scheduler):
    real_scheduler()
    pub = programar(session, "c-210", 60)

    job = get_scheduler().get_job(job_id_for(pub.id))
    assert job is not None
    assert job.next_run_time == pub.schedule_at
    assert job.args == (pub.id,)


def test_registrar_dos_veces_no_duplica_el_job(session, events, real_scheduler):
    real_scheduler()
    pub = programar(session, "c-211", 60)
    nueva_hora = pub.schedule_at + timedelta(minutes=5)

    schedule_job(pub.id, nueva_hora)

    assert len(publication_jobs()) == 1
    assert get_scheduler().get_job(job_id_for(pub.id)).next_run_time == nueva_hora


def test_la_reconciliacion_no_cuenta_como_job_pendiente(session, events, real_scheduler, client):
    real_scheduler()
    programar(session, "c-212", 60)

    assert get_scheduler().get_job(RECONCILE_JOB_ID, jobstore=INTERNAL_JOBSTORE) is not None
    assert client.get("/api/health").json()["data"]["pendingJobs"] == 1


# --- Cancelación --------------------------------------------------------------


def test_cancelar_quita_el_job_y_no_publica(session, events, real_scheduler, reloj):
    real_scheduler()
    pub = programar(session, "c-220", 1.0)

    cancelada = cancel_publication(session, pub.id)

    assert cancelada.state == PublishState.CANCELLED.value
    assert get_scheduler().get_job(job_id_for(pub.id)) is None
    time.sleep(1.5)
    session.expire_all()
    assert session.get(Publication, pub.id).state == PublishState.CANCELLED.value
    assert reloj.llamadas == []
    assert events.events_of_type("publish.completed") == []


def test_cancelar_sin_scheduler_activo_cambia_el_estado(session, events, publisher):
    pub = programar(session, "c-221", 60)

    cancelada = cancel_publication(session, pub.id)

    assert cancelada.state == PublishState.CANCELLED.value


def test_no_se_cancela_una_publicacion_ya_publicada(session, events, real_scheduler, reloj):
    real_scheduler()
    pub = programar(session, "c-222", 0.5)
    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)

    with pytest.raises(ScheduleConflictError):
        cancel_publication(session, pub.id)


def test_cancelar_dos_veces_falla_la_segunda(session, events, publisher):
    pub = programar(session, "c-223", 60)
    cancel_publication(session, pub.id)

    with pytest.raises(ScheduleConflictError):
        cancel_publication(session, pub.id)


def test_cancelar_publicacion_inexistente(session):
    with pytest.raises(PublicationNotFoundError):
        cancel_publication(session, "no-existe")


def test_cancelar_libera_el_contenido_para_reprogramar(session, events, publisher):
    pub = programar(session, "c-224", 60)
    cancel_publication(session, pub.id)

    nueva = programar(session, "c-224", 120)

    assert nueva.state == PublishState.PENDING.value


def test_pending_a_failed_es_transicion_valida():
    """ADR-0004: la publicación vencida pasa de pending a failed."""
    assert can_transition(PublishState.PENDING, PublishState.FAILED) is True
