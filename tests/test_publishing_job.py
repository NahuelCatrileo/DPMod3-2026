"""US-C2 · Subtarea 2.5 (parcial) — Pruebas del job handler.

Lo que estas pruebas cubren: ejecución, idempotencia, camino de error y
transiciones de estado.

Lo que NO cubren y falta para cerrar US-C2: reinicio real del contenedor con
job store persistente. Eso necesita testcontainers o un test de integración
con docker compose, y va en el Sprint 2.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.errors import ErrorCode
from app.domain.states import (
    InvalidTransitionError,
    PublishState,
    assert_transition,
    can_transition,
)
from app.infra.models import Publication
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.mock import MockPublisher
from app.services.publishing import execute_publication
from app.services.scheduling import schedule_publication


def _crear_pendiente(session, events, content_id="c-100") -> Publication:
    return schedule_publication(
        session,
        content_id=content_id,
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        tz_name="America/Santiago",
    )


def test_job_publica_y_emite_publish_completed(session, events, publisher):
    pub = _crear_pendiente(session, events)
    events.clear()

    execute_publication(pub.id)

    session.expire_all()
    actualizada = session.get(Publication, pub.id)
    assert actualizada.state == PublishState.PUBLISHED.value
    assert actualizada.youtube_video_id
    assert actualizada.attempts == 1
    assert actualizada.last_error is None

    completados = events.events_of_type("publish.completed")
    assert len(completados) == 1
    assert set(completados[0]["payload"]) == {
        "contentId",
        "youtubeVideoId",
        "publishedAt",
    }


def test_job_es_idempotente(session, events, publisher):
    """Un segundo disparo no debe publicar de nuevo. Es lo que protege al
    creador de que su video se suba dos veces tras un reinicio."""
    pub = _crear_pendiente(session, events, "c-101")
    events.clear()

    execute_publication(pub.id)
    execute_publication(pub.id)
    execute_publication(pub.id)

    session.expire_all()
    actualizada = session.get(Publication, pub.id)
    assert actualizada.attempts == 1
    assert len(events.events_of_type("publish.completed")) == 1


def test_fallo_del_publicador_emite_publish_failed(session, events):
    set_publisher(MockPublisher(latency_seconds=0, failure_rate=1.0))
    try:
        pub = _crear_pendiente(session, events, "c-102")
        events.clear()

        execute_publication(pub.id)

        session.expire_all()
        actualizada = session.get(Publication, pub.id)
        assert actualizada.state == PublishState.FAILED.value
        assert actualizada.last_error.startswith(ErrorCode.QUOTA_EXCEEDED.value)

        fallidos = events.events_of_type("publish.failed")
        assert len(fallidos) == 1
        assert set(fallidos[0]["payload"]) == {
            "contentId",
            "errorCode",
            "attempt",
            "reason",
        }
        assert fallidos[0]["payload"]["errorCode"] == ErrorCode.QUOTA_EXCEEDED.value
        assert fallidos[0]["payload"]["attempt"] == 1
    finally:
        set_publisher(None)


def test_job_sobre_publicacion_inexistente_no_revienta(session, events, publisher):
    execute_publication("no-existe")
    assert events.published == []


def test_todos_los_eventos_comparten_el_correlation_id(session, events, publisher):
    pub = _crear_pendiente(session, events, "c-103")
    execute_publication(pub.id)
    ids = {e["correlationId"] for e in events.published}
    assert len(ids) == 1


# --- Máquina de estados (US-C6) -------------------------------------------


@pytest.mark.parametrize(
    "origen,destino,esperado",
    [
        (PublishState.PENDING, PublishState.PUBLISHING, True),
        (PublishState.PENDING, PublishState.CANCELLED, True),
        (PublishState.PENDING, PublishState.PUBLISHED, False),
        (PublishState.PUBLISHING, PublishState.PUBLISHED, True),
        (PublishState.PUBLISHING, PublishState.FAILED, True),
        (PublishState.PUBLISHED, PublishState.PUBLISHING, False),
        (PublishState.FAILED, PublishState.PUBLISHING, True),
        (PublishState.CANCELLED, PublishState.PUBLISHING, False),
    ],
)
def test_matriz_de_transiciones(origen, destino, esperado):
    assert can_transition(origen, destino) is esperado


def test_transicion_invalida_lanza_error():
    with pytest.raises(InvalidTransitionError):
        assert_transition(PublishState.PUBLISHED, PublishState.PENDING)
