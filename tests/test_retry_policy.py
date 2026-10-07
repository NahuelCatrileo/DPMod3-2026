"""US-C5 · Política de reintentos: backoff, límite de intentos y eventos.

Cubre las dos ramas del handler:
  - reintentable  -> publishing vuelve a pending con job nuevo, SIN evento
  - definitivo    -> failed + publish.failed (único punto de emisión)
"""

from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.domain.errors import ErrorClass, ErrorCode
from app.infra.models import Publication
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.mock import MockPublisher
from app.services import retry
from app.services.publishing import execute_publication
from app.services.scheduling import schedule_publication


def _fallo(error_code: ErrorCode) -> MockPublisher:
    return MockPublisher(latency_seconds=0, failure_rate=1.0, error_code=error_code)


def _crear_pendiente(session, content_id: str) -> Publication:
    return schedule_publication(
        session,
        content_id=content_id,
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        tz_name="America/Santiago",
    )


# --- should_retry / retry_delay_seconds (pura) --------------------------------


def test_un_error_definitivo_nunca_se_reintenta(monkeypatch):
    monkeypatch.setattr(get_settings(), "PUBLISH_MAX_ATTEMPTS", 10)
    assert retry.should_retry(ErrorClass.DEFINITIVO, attempts=1) is False


def test_transitorio_reintenta_mientras_queden_intentos(monkeypatch):
    monkeypatch.setattr(get_settings(), "PUBLISH_MAX_ATTEMPTS", 3)
    assert retry.should_retry(ErrorClass.TRANSITORIO, attempts=1) is True
    assert retry.should_retry(ErrorClass.TRANSITORIO, attempts=2) is True
    assert retry.should_retry(ErrorClass.TRANSITORIO, attempts=3) is False


def test_diferible_tambien_respeta_el_limite(monkeypatch):
    monkeypatch.setattr(get_settings(), "PUBLISH_MAX_ATTEMPTS", 2)
    assert retry.should_retry(ErrorClass.DIFERIBLE, attempts=1) is True
    assert retry.should_retry(ErrorClass.DIFERIBLE, attempts=2) is False


def test_backoff_exponencial_para_transitorios(monkeypatch):
    monkeypatch.setattr(get_settings(), "PUBLISH_RETRY_BACKOFF_SECONDS", 60)
    assert retry.retry_delay_seconds(ErrorClass.TRANSITORIO, attempts=1) == 60
    assert retry.retry_delay_seconds(ErrorClass.TRANSITORIO, attempts=2) == 120
    assert retry.retry_delay_seconds(ErrorClass.TRANSITORIO, attempts=3) == 240


def test_la_cuota_usa_su_propio_delay(monkeypatch):
    """La cuota se restablece por ventana: no sirve reintentar cada minuto."""
    monkeypatch.setattr(get_settings(), "PUBLISH_RETRY_BACKOFF_SECONDS", 60)
    monkeypatch.setattr(get_settings(), "PUBLISH_QUOTA_RETRY_SECONDS", 3600)
    assert retry.retry_delay_seconds(ErrorClass.DIFERIBLE, attempts=1) == 3600
    assert retry.retry_delay_seconds(ErrorClass.DIFERIBLE, attempts=2) == 3600


# --- Handler: camino transitorio ---------------------------------------------


def test_fallo_transitorio_vuelve_a_pending_sin_evento(session, events, monkeypatch):
    """CA1 de US-C5: se reintenta, y publish.failed NO se emite todavía."""
    programados = {}
    _capturar_jobs(programados, monkeypatch)
    set_publisher(_fallo(ErrorCode.PUBLISH_FAILED))
    try:
        pub = _crear_pendiente(session, "c-r1")
        events.clear()

        execute_publication(pub.id)

        session.expire_all()
        actualizada = session.get(Publication, pub.id)
        assert actualizada.state == "pending"
        assert actualizada.attempts == 1
        assert actualizada.last_error.startswith(ErrorCode.PUBLISH_FAILED.value)
        assert events.events_of_type("publish.failed") == []
        assert events.events_of_type("publish.completed") == []
        # Job reprogramado con el backoff del primer intento.
        retraso = programados["run_at"] - datetime.now(timezone.utc)
        assert timedelta(seconds=55) < retraso <= timedelta(seconds=65)
    finally:
        set_publisher(None)


def test_fallo_diferible_usa_el_delay_de_cuota(session, events, monkeypatch):
    programados = {}
    _capturar_jobs(programados, monkeypatch)
    set_publisher(_fallo(ErrorCode.QUOTA_EXCEEDED))
    try:
        pub = _crear_pendiente(session, "c-r2")
        events.clear()
        execute_publication(pub.id)

        session.expire_all()
        assert session.get(Publication, pub.id).state == "pending"
        assert events.events_of_type("publish.failed") == []
        assert events.events_of_type("publish.completed") == []
        retraso = programados["run_at"] - datetime.now(timezone.utc)
        assert timedelta(minutes=59) < retraso <= timedelta(minutes=61)
    finally:
        set_publisher(None)


def test_agotados_los_intentos_emite_publish_failed_una_vez(session, events, monkeypatch):
    """CA2 de US-C5: superado el límite, failed + publish.failed con causa."""
    _capturar_jobs({}, monkeypatch)
    max_intentos = get_settings().PUBLISH_MAX_ATTEMPTS
    set_publisher(_fallo(ErrorCode.PUBLISH_FAILED))
    try:
        pub = _crear_pendiente(session, "c-r3")
        events.clear()

        for _ in range(max_intentos):
            execute_publication(pub.id)

        session.expire_all()
        actualizada = session.get(Publication, pub.id)
        assert actualizada.state == "failed"
        assert actualizada.attempts == max_intentos
        assert actualizada.last_error.startswith(ErrorCode.PUBLISH_FAILED.value)

        fallidos = events.events_of_type("publish.failed")
        assert len(fallidos) == 1
        assert fallidos[0]["payload"]["attempt"] == max_intentos
        assert fallidos[0]["payload"]["errorCode"] == ErrorCode.PUBLISH_FAILED.value
    finally:
        set_publisher(None)


def test_un_intento_exitoso_tras_reintentos_publica(session, events, monkeypatch):
    """El camino completo: falla, reintenta y al final publica."""
    _capturar_jobs({}, monkeypatch)
    publicador = _fallo(ErrorCode.PUBLISH_FAILED)
    set_publisher(publicador)
    try:
        pub = _crear_pendiente(session, "c-r4")
        events.clear()

        execute_publication(pub.id)  # falla -> pending
        publicador.failure_rate = 0.0  # "se resolvió" el fallo transitorio
        execute_publication(pub.id)  # reintento -> publica

        session.expire_all()
        actualizada = session.get(Publication, pub.id)
        assert actualizada.state == "published"
        assert actualizada.attempts == 2
        assert len(events.events_of_type("publish.completed")) == 1
        assert events.events_of_type("publish.failed") == []
    finally:
        set_publisher(None)


def test_excepcion_del_publicador_es_transitorio(session, events, monkeypatch):
    """Una excepción no controlada también clasifica como transitorio."""
    _capturar_jobs({}, monkeypatch)

    class PublicadorRoto:
        def publish_video(self, content_id):
            raise RuntimeError("se cayó el publicador")

    set_publisher(PublicadorRoto())
    try:
        pub = _crear_pendiente(session, "c-r5")
        events.clear()

        execute_publication(pub.id)

        session.expire_all()
        actualizada = session.get(Publication, pub.id)
        assert actualizada.state == "pending"
        assert actualizada.attempts == 1
        assert actualizada.last_error.startswith(ErrorCode.PUBLISH_FAILED.value)
        assert events.events_of_type("publish.failed") == []
    finally:
        set_publisher(None)


def _capturar_jobs(destino: dict, monkeypatch) -> None:
    """Reemplaza schedule_job para observar la hora del próximo intento."""
    import app.scheduler.scheduler as sched

    def _fake(publish_id: str, run_at_utc: datetime) -> str:
        destino["run_at"] = run_at_utc
        return f"publish:{publish_id}"

    monkeypatch.setattr(sched, "schedule_job", _fake)
