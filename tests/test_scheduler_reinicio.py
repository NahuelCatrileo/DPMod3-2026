"""US-C2 · TECH-C2.6 — Pruebas de recuperación tras reinicio.

Criterio de aceptación: "se recupera correctamente tras un reinicio (job
store persistente)". Las dos primeras pruebas matan un proceso real con
os._exit (sin apagado ordenado, como un contenedor que se cae) y comprueban
que otro proceso retoma el trabajo desde la base de datos.
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import update

from app.config import get_settings
from app.domain.errors import ErrorCode
from app.domain.states import PublishState
from app.infra.models import Publication
from app.scheduler.scheduler import get_scheduler, job_id_for, publication_jobs
from tests.helpers_scheduler import esperar_estado, programar, stop_scheduler

RAIZ = Path(__file__).resolve().parent.parent


def correr_proceso(modo: str, segundos: float, **env_extra: str) -> str:
    """Lanza tests/proceso_scheduler.py, que programa y muere con os._exit."""
    env = {**os.environ, "SCHEDULER_ENABLED": "true", **env_extra}
    salida = subprocess.run(
        [sys.executable, "-m", "tests.proceso_scheduler", modo, str(segundos)],
        cwd=RAIZ,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert salida.returncode == 0, salida.stderr
    return salida.stdout.strip().splitlines()[-1]


# --- Caída real del proceso ---------------------------------------------------


def test_job_sobrevive_a_la_caida_del_proceso_y_se_dispara(session, events, real_scheduler):
    publish_id = correr_proceso("programar", 2)

    session.expire_all()
    assert session.get(Publication, publish_id).state == PublishState.PENDING.value

    real_scheduler()
    assert [j.id for j in publication_jobs()] == [job_id_for(publish_id)]

    esperar_estado(session, publish_id, PublishState.PUBLISHED.value)
    assert len(events.events_of_type("publish.completed")) == 1


def test_caida_a_mitad_de_la_publicacion_se_reencola_para_reintentar(
    session, events, real_scheduler
):
    """US-C5: la interrupción es un fallo transitorio. No se emite
    publish.failed hasta que se agoten los intentos."""
    publish_id = correr_proceso("colgar", 0.5, MOCK_LATENCY_SECONDS="30")

    session.expire_all()
    assert session.get(Publication, publish_id).state == PublishState.PUBLISHING.value

    real_scheduler()

    session.expire_all()
    pub = session.get(Publication, publish_id)
    assert pub.state == PublishState.PENDING.value
    assert pub.attempts == 1
    assert pub.last_error.startswith(ErrorCode.PUBLISH_FAILED.value)
    assert get_scheduler().get_job(job_id_for(publish_id)) is not None
    assert events.events_of_type("publish.failed") == []


# --- Reinicio dentro del mismo proceso ------------------------------------------


def test_reinicio_ordenado_conserva_el_job(session, events, real_scheduler):
    real_scheduler()
    pub = programar(session, "c-300", 2)
    stop_scheduler()
    assert get_scheduler() is None

    real_scheduler()

    assert get_scheduler().get_job(job_id_for(pub.id)) is not None
    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)


def test_publicacion_sin_job_se_reprograma_al_arrancar(session, events, real_scheduler):
    # Scheduler apagado: la fila se guarda pero el job nunca se registra.
    pub = programar(session, "c-301", 1.5)

    real_scheduler()

    assert get_scheduler().get_job(job_id_for(pub.id)) is not None
    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)


def test_caida_dentro_del_margen_publica_al_volver(session, events, real_scheduler):
    real_scheduler()
    pub = programar(session, "c-302", 0.5)
    stop_scheduler()
    time.sleep(1.0)  # la hora pasa con el scheduler detenido

    real_scheduler()

    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)
    assert events.events_of_type("publish.failed") == []


def test_caida_mas_larga_que_el_margen_termina_en_failed(
    session, events, real_scheduler, monkeypatch
):
    monkeypatch.setattr(get_settings(), "SCHEDULER_MISFIRE_GRACE_SECONDS", 1)
    real_scheduler()
    pub = programar(session, "c-303", 0.3)
    stop_scheduler()
    time.sleep(1.8)  # atraso mayor que el margen de 1 s

    real_scheduler()

    session.expire_all()
    actualizada = session.get(Publication, pub.id)
    assert actualizada.state == PublishState.FAILED.value
    assert actualizada.youtube_video_id is None
    assert publication_jobs() == []
    fallidos = events.events_of_type("publish.failed")
    assert len(fallidos) == 1
    assert fallidos[0]["payload"]["attempt"] == 0
    assert events.events_of_type("publish.completed") == []


def test_reconciliacion_periodica_repone_un_job_perdido(
    session, events, real_scheduler, monkeypatch
):
    monkeypatch.setattr(get_settings(), "SCHEDULER_RECONCILE_SECONDS", 1)
    real_scheduler()
    pub = programar(session, "c-304", 2.5)
    get_scheduler().remove_job(job_id_for(pub.id))

    esperar_estado(session, pub.id, PublishState.PUBLISHED.value)


def test_un_job_viejo_de_una_publicacion_ya_publicada_no_republica(session, events, real_scheduler):
    """Si tras un reinicio queda un job para una publicación que ya terminó,
    el UPDATE condicional del handler impide publicar dos veces."""
    pub = programar(session, "c-305", 60)
    session.execute(
        update(Publication)
        .where(Publication.id == pub.id)
        .values(state=PublishState.PUBLISHED.value, youtube_video_id="ya-estaba")
    )
    session.commit()

    real_scheduler()
    get_scheduler().add_job(
        "app.services.publishing:execute_publication",
        args=[pub.id],
        id=job_id_for(pub.id),
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=0.3),
    )
    time.sleep(1.0)

    session.expire_all()
    assert session.get(Publication, pub.id).youtube_video_id == "ya-estaba"
    assert events.events_of_type("publish.completed") == []
