"""US-C4 · Subtarea 4.4 — Pruebas del publicador simulado."""

import pytest

from app.domain.errors import ErrorCode
from app.infra.publishers.base import Publisher, PublishResult
from app.infra.publishers.factory import build_publisher
from app.infra.publishers.mock import MockPublisher
from app.infra.publishers.youtube import YouTubePublisher


def test_mock_implementa_la_interfaz():
    assert issubclass(MockPublisher, Publisher)
    assert issubclass(YouTubePublisher, Publisher)


def test_publicacion_exitosa_devuelve_id_de_youtube():
    resultado = MockPublisher(latency_seconds=0, failure_rate=0).publish_video("c-001")
    assert resultado.success is True
    assert resultado.youtube_video_id is not None
    assert len(resultado.youtube_video_id) == 11
    assert resultado.error_code is None


def test_el_mock_es_determinista():
    """Mismo contentId -> mismo videoId. Sin esto la demo no es reproducible."""
    a = MockPublisher(latency_seconds=0, failure_rate=0).publish_video("c-001")
    b = MockPublisher(latency_seconds=0, failure_rate=0).publish_video("c-001")
    assert a.youtube_video_id == b.youtube_video_id


def test_contenidos_distintos_dan_ids_distintos():
    p = MockPublisher(latency_seconds=0, failure_rate=0)
    assert p.publish_video("c-001").youtube_video_id != p.publish_video(
        "c-002"
    ).youtube_video_id


def test_fallo_simulado_devuelve_codigo_registrado():
    """failure_rate=1.0 fuerza el camino de error."""
    resultado = MockPublisher(latency_seconds=0, failure_rate=1.0).publish_video("c-001")
    assert resultado.success is False
    assert resultado.error_code is ErrorCode.QUOTA_EXCEEDED
    assert resultado.reason
    assert resultado.youtube_video_id is None


def test_el_fallo_tambien_es_determinista():
    p = MockPublisher(latency_seconds=0, failure_rate=0.5)
    resultados = {p.publish_video("c-repetido").success for _ in range(10)}
    assert len(resultados) == 1


# --- US-C5 · fallas transitorias controlables (MOCK_FAILURE_ATTEMPTS) --------


def test_falla_las_primeras_n_veces_y_despues_publica():
    """El caso central: un fallo transitorio que se cura solo, para poder
    ensayar el ciclo completo falla -> backoff -> publish.completed."""
    p = MockPublisher(
        latency_seconds=0,
        failure_rate=1.0,
        failure_attempts=2,
        error_code=ErrorCode.PUBLISH_FAILED,
    )
    primero = p.publish_video("c-001")
    segundo = p.publish_video("c-001")
    tercero = p.publish_video("c-001")

    assert primero.success is False
    assert primero.error_code is ErrorCode.PUBLISH_FAILED
    assert segundo.success is False
    assert tercero.success is True
    assert tercero.youtube_video_id is not None


def test_failure_attempts_cero_sigue_fallando_siempre():
    """Comportamiento previo intacto: sin límite, los reintentos agotan."""
    p = MockPublisher(latency_seconds=0, failure_rate=1.0, failure_attempts=0)
    for _ in range(5):
        assert p.publish_video("c-001").success is False


def test_el_contador_de_fallos_es_por_content_id():
    p = MockPublisher(
        latency_seconds=0, failure_rate=1.0, failure_attempts=1, error_code=ErrorCode.PUBLISH_FAILED
    )
    assert p.publish_video("c-a").success is False
    assert p.publish_video("c-b").success is False  # b todavía no falló
    assert p.publish_video("c-a").success is True
    assert p.publish_video("c-b").success is True


def test_failure_attempts_por_defecto_viene_de_settings(monkeypatch):
    """La política también se configura por variable de entorno."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "MOCK_FAILURE_ATTEMPTS", 1)
    p = MockPublisher(latency_seconds=0, failure_rate=1.0, error_code=ErrorCode.PUBLISH_FAILED)

    assert p.failure_attempts == 1
    assert p.publish_video("c-env").success is False
    assert p.publish_video("c-env").success is True


def test_el_publicador_no_emite_eventos(events):
    """Criterio de aceptación: mock y real deben emitir lo mismo.
    Se cumple porque NINGUNO de los dos emite: emite services/publishing.py."""
    MockPublisher(latency_seconds=0, failure_rate=0).publish_video("c-001")
    assert events.published == []


# --- Subtarea 4.3: feature flag -------------------------------------------


def test_flag_mock_construye_mockpublisher():
    assert isinstance(build_publisher("mock"), MockPublisher)


def test_flag_youtube_construye_youtubepublisher():
    assert isinstance(build_publisher("youtube"), YouTubePublisher)


def test_flag_invalido_falla_temprano():
    with pytest.raises(ValueError):
        build_publisher("cualquier-cosa")


def test_youtube_publisher_aun_no_implementado():
    resultado = YouTubePublisher().publish_video("c-001")
    assert resultado.success is False
    assert resultado.error_code is ErrorCode.PUBLISH_FAILED


def test_publish_result_helpers():
    assert PublishResult.ok("abc").success is True
    assert PublishResult.failure(ErrorCode.OAUTH_ERROR, "x").success is False
