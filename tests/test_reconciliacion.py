"""US-C2 · TECH-C2.4 — Reconciliación tabla publication <-> job store.

Pruebas deterministas: el scheduler queda en pausa (no dispara nada) y la
reconciliación recibe `now` explícito, así que no hay esperas ni carreras.
Cubren cada rama de la decisión de ADR-0004.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.domain.states import PublishState
from app.infra.models import Publication
from app.scheduler import scheduler as sched
from app.scheduler.scheduler import build_scheduler, job_id_for, schedule_job
from app.services.recovery import reconcile_publications

AHORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def pausado(monkeypatch, real_scheduler):
    s = build_scheduler()
    s.start(paused=True)
    monkeypatch.setattr(sched, "_scheduler", s)
    yield s
    # Al apagarse, APScheduler procesa una vez los jobs vencidos aunque esté en
    # pausa; se vacía antes para que no intente ejecutarlos.
    s.remove_all_jobs()
    s.shutdown(wait=False)


def fila(session, estado: PublishState, schedule_at: datetime, attempts: int = 0) -> Publication:
    pub = Publication(
        id=str(uuid.uuid4()),
        content_id=f"c-{uuid.uuid4().hex[:8]}",
        state=estado.value,
        schedule_at=schedule_at,
        timezone="America/Santiago",
        attempts=attempts,
        correlation_id=str(uuid.uuid4()),
    )
    session.add(pub)
    session.commit()
    return pub


def estado(session, publish_id: str) -> str:
    session.expire_all()
    return session.get(Publication, publish_id).state


def margen() -> timedelta:
    return timedelta(seconds=get_settings().SCHEDULER_MISFIRE_GRACE_SECONDS)


def test_pendiente_vencida_pasa_a_failed_y_emite_evento(session, events, pausado):
    pub = fila(session, PublishState.PENDING, AHORA - margen() - timedelta(seconds=1))
    schedule_job(pub.id, pub.schedule_at)

    reporte = reconcile_publications(now=AHORA)

    assert reporte.vencidas == [pub.id]
    assert estado(session, pub.id) == PublishState.FAILED.value
    assert pausado.get_job(job_id_for(pub.id)) is None
    (evento,) = events.events_of_type("publish.failed")
    assert evento["payload"]["errorCode"] == "PUBLISH_FAILED"
    assert evento["payload"]["attempt"] == 0
    assert evento["payload"]["contentId"] == pub.content_id
    assert evento["correlationId"] == pub.correlation_id


def test_pendiente_justo_en_el_limite_del_margen_no_vence(session, events, pausado):
    pub = fila(session, PublishState.PENDING, AHORA - margen())
    schedule_job(pub.id, pub.schedule_at)

    reporte = reconcile_publications(now=AHORA)

    assert reporte.vencidas == []
    assert estado(session, pub.id) == PublishState.PENDING.value


def test_pendiente_con_job_no_se_toca(session, events, pausado):
    pub = fila(session, PublishState.PENDING, AHORA + timedelta(hours=1))
    schedule_job(pub.id, pub.schedule_at)

    reporte = reconcile_publications(now=AHORA)

    assert reporte.reprogramadas == reporte.vencidas == []
    assert pausado.get_job(job_id_for(pub.id)).next_run_time == pub.schedule_at
    assert events.published == []


def test_pendiente_futura_sin_job_se_reprograma_a_su_hora(session, events, pausado):
    pub = fila(session, PublishState.PENDING, AHORA + timedelta(hours=1))

    reporte = reconcile_publications(now=AHORA)

    assert reporte.reprogramadas == [pub.id]
    assert pausado.get_job(job_id_for(pub.id)).next_run_time == pub.schedule_at


def test_pendiente_atrasada_dentro_del_margen_sin_job_se_dispara_ya(session, events, pausado):
    pub = fila(session, PublishState.PENDING, AHORA - timedelta(minutes=10))

    reporte = reconcile_publications(now=AHORA)

    assert reporte.reprogramadas == [pub.id]
    assert pausado.get_job(job_id_for(pub.id)).next_run_time == AHORA


def test_pendiente_esperando_reintento_no_vence_y_usa_el_backoff(session, events, pausado):
    """US-C5: con attempts > 0 la fila no está 'vencida', espera su
    reintento. Si se pierde el job, se recrea con updated_at + backoff
    (transitorio), no con schedule_at, que quedó viejo."""
    pub = fila(session, PublishState.PENDING, AHORA - timedelta(days=2), attempts=1)
    pub.updated_at = AHORA - timedelta(seconds=10)
    session.commit()

    reporte = reconcile_publications(now=AHORA)

    assert reporte.vencidas == []
    assert reporte.reprogramadas == [pub.id]
    backoff = timedelta(seconds=get_settings().PUBLISH_RETRY_BACKOFF_SECONDS)
    assert pausado.get_job(job_id_for(pub.id)).next_run_time == pub.updated_at + backoff
    assert events.published == []


def test_pendiente_reintentando_con_error_de_cuota_usa_su_delay(session, events, pausado):
    """El delay se deduce de la clase del último error guardado en last_error."""
    pub = fila(session, PublishState.PENDING, AHORA - timedelta(days=2), attempts=2)
    pub.updated_at = AHORA
    pub.last_error = "QUOTA_EXCEEDED: has superado la cuota de publicaciones"
    session.commit()

    reporte = reconcile_publications(now=AHORA)

    assert reporte.vencidas == []
    assert reporte.reprogramadas == [pub.id]
    cuota = timedelta(seconds=get_settings().PUBLISH_QUOTA_RETRY_SECONDS)
    assert pausado.get_job(job_id_for(pub.id)).next_run_time == AHORA + cuota
    assert events.published == []


def test_publishing_al_arrancar_vuelve_a_pending_para_reintentar(session, events, pausado):
    """US-C5: la interrupción es transitorio; quedan intentos, así que la
    publicación se reencola en vez de fallar."""
    pub = fila(session, PublishState.PUBLISHING, AHORA - timedelta(minutes=1), attempts=1)

    reporte = reconcile_publications(now=AHORA, startup=True)

    assert reporte.interrumpidas == [pub.id]
    assert reporte.reprogramadas == [pub.id]
    assert estado(session, pub.id) == PublishState.PENDING.value
    assert pausado.get_job(job_id_for(pub.id)) is not None
    assert events.published == []


def test_publishing_al_arrancar_sin_intentos_disponibles_pasa_a_failed(
    session, events, pausado
):
    """Con el límite agotado, la interrupción termina como en ADR-0004."""
    pub = fila(
        session,
        PublishState.PUBLISHING,
        AHORA - timedelta(minutes=1),
        attempts=get_settings().PUBLISH_MAX_ATTEMPTS,
    )

    reporte = reconcile_publications(now=AHORA, startup=True)

    assert reporte.interrumpidas == [pub.id]
    assert reporte.reprogramadas == []
    assert estado(session, pub.id) == PublishState.FAILED.value
    (evento,) = events.events_of_type("publish.failed")
    assert evento["payload"]["attempt"] == get_settings().PUBLISH_MAX_ATTEMPTS


def test_publishing_en_la_ronda_periodica_no_se_toca(session, events, pausado):
    """Fuera del arranque, 'publishing' puede ser un job que está corriendo."""
    pub = fila(session, PublishState.PUBLISHING, AHORA - timedelta(minutes=1), attempts=1)

    reporte = reconcile_publications(now=AHORA)

    assert reporte.interrumpidas == []
    assert estado(session, pub.id) == PublishState.PUBLISHING.value


@pytest.mark.parametrize(
    "terminal",
    [PublishState.PUBLISHED, PublishState.FAILED, PublishState.CANCELLED],
)
def test_estados_terminales_no_se_tocan(session, events, pausado, terminal):
    pub = fila(session, terminal, AHORA - timedelta(days=1))

    reconcile_publications(now=AHORA, startup=True)

    assert estado(session, pub.id) == terminal.value
    assert pausado.get_job(job_id_for(pub.id)) is None
    assert events.published == []


def test_reconciliar_dos_veces_emite_un_solo_evento(session, events, pausado):
    fila(session, PublishState.PENDING, AHORA - timedelta(days=1))
    # Intentos agotados: es la única forma de que una interrupción termine
    # en failed (si no, US-C5 la reencola y no emite evento).
    fila(
        session,
        PublishState.PUBLISHING,
        AHORA - timedelta(minutes=1),
        attempts=get_settings().PUBLISH_MAX_ATTEMPTS,
    )

    reconcile_publications(now=AHORA, startup=True)
    reconcile_publications(now=AHORA, startup=True)

    assert len(events.events_of_type("publish.failed")) == 2


def test_sin_scheduler_igual_marca_las_vencidas(session, events, publisher):
    vencida = fila(session, PublishState.PENDING, AHORA - timedelta(days=1))
    futura = fila(session, PublishState.PENDING, AHORA + timedelta(hours=1))

    reporte = reconcile_publications(now=AHORA)

    assert reporte.vencidas == [vencida.id]
    assert reporte.reprogramadas == []
    assert estado(session, futura.id) == PublishState.PENDING.value
