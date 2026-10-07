"""US-C5 · Contrato de los eventos de resultado (Doc 1 §6.3 · eventos.md).

La forma exacta de `publish.failed` y `publish.completed` es lo que
consumen los demás equipos, así que se fija acá en cada escenario de
error, no solo en los docs. El sobre de `publish.scheduled` ya lo cubre
tests/test_schedule_api.py.

Escenarios cubiertos:
  - error definitivo            -> publish.failed con attempt = 1
  - cuota con intentos agotados -> publish.failed con attempt = max
  - publicación vencida         -> publish.failed con attempt = 0 (ADR-0004)
  - fallo transitorio que se cura -> publish.completed, sin publish.failed
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.domain.errors import ErrorCode
from app.domain.states import PublishState
from app.infra.models import Publication
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.mock import MockPublisher
from app.services.publishing import execute_publication
from app.services.recovery import reconcile_publications
from app.services.scheduling import schedule_publication

CLAVES_ENVELOPE = {
    "id",
    "type",
    "version",
    "timestamp",
    "correlationId",
    "causationId",
    "source",
    "payload",
}
CLAVES_FAILED = {"contentId", "errorCode", "attempt", "reason"}
CLAVES_COMPLETED = {"contentId", "youtubeVideoId", "publishedAt"}
CODIGOS_REGISTRADOS = {codigo.value for codigo in ErrorCode}
CORRELATION_ID = "corr-contrato-us-c5"


def _fallo(error_code: ErrorCode, failure_attempts: int = 0) -> MockPublisher:
    return MockPublisher(
        latency_seconds=0,
        failure_rate=1.0,
        error_code=error_code,
        failure_attempts=failure_attempts,
    )


def _crear(session, content_id: str) -> Publication:
    return schedule_publication(
        session,
        content_id=content_id,
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        tz_name="America/Santiago",
        correlation_id=CORRELATION_ID,
    )


def _contrato_failed(envelope: dict) -> None:
    """Sobre y payload exactos de publish.failed (eventos.md)."""
    assert set(envelope) == CLAVES_ENVELOPE
    assert envelope["type"] == "publish.failed"
    assert envelope["version"] == 1
    assert envelope["source"] == "module-3"
    assert envelope["timestamp"].endswith("Z")
    # El evento no responde a uno previo: se causa a sí mismo (§3.1).
    assert envelope["causationId"] == envelope["id"]
    assert envelope["correlationId"] == CORRELATION_ID

    payload = envelope["payload"]
    assert set(payload) == CLAVES_FAILED
    assert payload["contentId"]
    assert payload["errorCode"] in CODIGOS_REGISTRADOS
    assert isinstance(payload["attempt"], int)
    assert payload["reason"]


def _sin_publicacion(session, content_id: str, schedule_at: datetime) -> Publication:
    """Fila directa en la tabla, para escenarios que la API no deja crear
    (p. ej. una publicación con fecha ya vencida)."""
    pub = Publication(
        id=str(uuid.uuid4()),
        content_id=content_id,
        state=PublishState.PENDING.value,
        schedule_at=schedule_at,
        timezone="America/Santiago",
        attempts=0,
        correlation_id=CORRELATION_ID,
    )
    session.add(pub)
    session.commit()
    return pub


# --- publish.failed -----------------------------------------------------------


def test_error_definitivo_publica_el_sobre_de_failed(session, events):
    """OAUTH_ERROR no se reintenta: attempt = 1 y payload exacto."""
    set_publisher(_fallo(ErrorCode.OAUTH_ERROR))
    try:
        pub = _crear(session, "c-ct-1")
        events.clear()

        execute_publication(pub.id)

        (envelope,) = events.events_of_type("publish.failed")
        _contrato_failed(envelope)
        assert envelope["payload"]["contentId"] == "c-ct-1"
        assert envelope["payload"]["errorCode"] == "OAUTH_ERROR"
        assert envelope["payload"]["attempt"] == 1
    finally:
        set_publisher(None)


def test_cuota_agotada_llega_al_maximo_de_intentos(session, events):
    """QUOTA_EXCEEDED se reintenta hasta el límite: un único publish.failed
    con attempt = PUBLISH_MAX_ATTEMPTS."""
    max_intentos = get_settings().PUBLISH_MAX_ATTEMPTS
    set_publisher(_fallo(ErrorCode.QUOTA_EXCEEDED))
    try:
        pub = _crear(session, "c-ct-2")
        events.clear()

        for _ in range(max_intentos):
            execute_publication(pub.id)

        (envelope,) = events.events_of_type("publish.failed")
        _contrato_failed(envelope)
        assert envelope["payload"]["errorCode"] == "QUOTA_EXCEEDED"
        assert envelope["payload"]["attempt"] == max_intentos
    finally:
        set_publisher(None)


def test_vencida_emite_attempt_cero(session, events):
    """ADR-0004: una publicación que nunca se intentó falla con attempt = 0."""
    _sin_publicacion(
        session,
        "c-ct-3",
        datetime.now(timezone.utc) - timedelta(days=1),
    )

    reconcile_publications()

    (envelope,) = events.events_of_type("publish.failed")
    _contrato_failed(envelope)
    assert envelope["payload"]["contentId"] == "c-ct-3"
    assert envelope["payload"]["errorCode"] == "PUBLISH_FAILED"
    assert envelope["payload"]["attempt"] == 0


def test_durante_los_reintentos_no_se_emite_nada(session, events):
    """Solo se emite el resultado final: ni failed ni completed por intento."""
    max_intentos = get_settings().PUBLISH_MAX_ATTEMPTS
    set_publisher(_fallo(ErrorCode.PUBLISH_FAILED))
    try:
        pub = _crear(session, "c-ct-4")
        events.clear()

        for _ in range(max_intentos - 1):
            execute_publication(pub.id)
            assert events.published == []

        execute_publication(pub.id)  # intento agotado

        (envelope,) = events.events_of_type("publish.failed")
        _contrato_failed(envelope)
        assert envelope["payload"]["attempt"] == max_intentos
        assert events.events_of_type("publish.completed") == []
    finally:
        set_publisher(None)


# --- publish.completed --------------------------------------------------------


def test_fallo_que_se_cura_cierra_con_completed(session, events):
    """Escenario transitorio completo: falla, reintenta y publica. El
    resultado es UN publish.completed con el sobre exacto y el mismo
    correlationId del publish.scheduled original."""
    set_publisher(_fallo(ErrorCode.PUBLISH_FAILED, failure_attempts=1))
    try:
        pub = _crear(session, "c-ct-5")
        events.clear()

        execute_publication(pub.id)  # falla -> reintento
        execute_publication(pub.id)  # reintento -> publica

        assert events.events_of_type("publish.failed") == []
        (envelope,) = events.events_of_type("publish.completed")
        assert set(envelope) == CLAVES_ENVELOPE
        assert envelope["type"] == "publish.completed"
        assert envelope["version"] == 1
        assert envelope["source"] == "module-3"
        assert envelope["timestamp"].endswith("Z")
        assert envelope["correlationId"] == CORRELATION_ID

        payload = envelope["payload"]
        assert set(payload) == CLAVES_COMPLETED
        assert payload["contentId"] == "c-ct-5"
        assert len(payload["youtubeVideoId"]) == 11
        assert payload["publishedAt"].endswith("Z")
    finally:
        set_publisher(None)
