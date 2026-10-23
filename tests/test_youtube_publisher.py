"""US-C3.5 · Publicación real en YouTube con subida reanudable.

Las pruebas no hablan con Google: reemplazan las credenciales, el cliente de la
API y el `Upload` de googleapiclient. Lo que se verifica es que `videos.insert`
reciba el cuerpo, la subida reanudable y que el `id` que devuelve YouTube
termine en `PublishResult` y en `publish.completed`.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.config import get_settings
from app.domain.errors import DomainError, ErrorCode, OAuthError
from app.infra.models import Publication
from app.infra.publishers.base import PublishResult
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.youtube import (
    VideoContent,
    YouTubePublisher,
    content_from_local_file,
)
from app.services.publishing import execute_publication
from app.services.scheduling import schedule_publication

VIDEO_ID = "dQw4w9WgXcQ"


class _Credenciales:
    """Lo único que el publicador hace con ellas es pasarlas al service."""


class _Progreso:
    def __init__(self, fraccion: float) -> None:
        self._fraccion = fraccion

    def progress(self) -> float:
        return self._fraccion


class _Peticion:
    """Imita el Upload request de googleapiclient con respuestas prefijadas."""

    def __init__(self, respuestas: list[tuple[_Progreso | None, dict | None]]) -> None:
        self._respuestas = list(respuestas)
        self.llamadas = 0

    def next_chunk(self):
        self.llamadas += 1
        return self._respuestas.pop(0)


class _PeticionQueFalla:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def next_chunk(self):
        raise self._error


class _Videos:
    def __init__(self, peticion) -> None:
        self._peticion = peticion
        self.kwargs: dict | None = None

    def insert(self, **kwargs):
        self.kwargs = kwargs
        return self._peticion


class _Servicio:
    def __init__(self, peticion) -> None:
        self.videos_service = _Videos(peticion)

    def videos(self) -> _Videos:
        return self.videos_service


def _contenido(tmp_path, titulo: str = "Video de prueba") -> VideoContent:
    archivo = tmp_path / "video.mp4"
    archivo.write_bytes(b"video de prueba")
    return VideoContent(
        title=titulo,
        file_path=archivo,
        description="Descripción de prueba",
        tags=("pubtube",),
    )


def _publicador(tmp_path, peticion) -> tuple[YouTubePublisher, _Servicio]:
    contenido = _contenido(tmp_path)
    servicio = _Servicio(peticion)
    publicador = YouTubePublisher(
        content_provider=lambda content_id: contenido,
        credentials_provider=lambda: _Credenciales(),
        service_factory=lambda credenciales: servicio,
    )
    return publicador, servicio


def _http_error(status: int, reason: str) -> HttpError:
    cuerpo = {
        "error": {
            "code": status,
            "message": "fallo de prueba",
            "errors": [{"message": "fallo de prueba", "reason": reason}],
        }
    }
    respuesta = Response({"status": status, "reason": reason, "content-type": "application/json"})
    return HttpError(respuesta, json.dumps(cuerpo).encode("utf-8"))


# --- Subida -----------------------------------------------------------------


def test_videos_insert_usa_subida_reanudable_y_devuelve_el_id(tmp_path):
    peticion = _Peticion([(_Progreso(0.5), None), (None, {"id": VIDEO_ID})])
    publicador, servicio = _publicador(tmp_path, peticion)

    resultado = publicador.publish_video("c-001")

    assert resultado == PublishResult.ok(VIDEO_ID)
    # Dos bloques: el primero informa progreso y el segundo termina.
    assert peticion.llamadas == 2

    kwargs = servicio.videos_service.kwargs
    assert kwargs is not None
    assert kwargs["part"] == "snippet,status"
    assert kwargs["body"]["snippet"]["title"] == "Video de prueba"
    assert kwargs["body"]["snippet"]["tags"] == ["pubtube"]
    assert kwargs["body"]["status"]["privacyStatus"] == "private"

    media = kwargs["media_body"]
    assert media.resumable() is True
    assert media.chunksize() == 5 * 1024 * 1024
    assert media.mimetype() == "video/mp4"


def test_respuesta_sin_id_es_un_fallo_transitorio(tmp_path):
    peticion = _Peticion([(None, {})])
    publicador, _ = _publicador(tmp_path, peticion)

    resultado = publicador.publish_video("c-001")

    assert resultado.success is False
    assert resultado.error_code is ErrorCode.PUBLISH_FAILED


def test_sin_archivo_no_se_llama_a_youtube(tmp_path):
    peticion = _Peticion([])
    servicio = _Servicio(peticion)
    contenido = VideoContent(title="x", file_path=tmp_path / "no-existe.mp4")
    publicador = YouTubePublisher(
        content_provider=lambda content_id: contenido,
        credentials_provider=lambda: _Credenciales(),
        service_factory=lambda credenciales: servicio,
    )

    resultado = publicador.publish_video("c-001")

    assert resultado.error_code is ErrorCode.CONTENT_NOT_FOUND
    assert servicio.videos_service.kwargs is None


def test_si_falla_la_credencial_se_devuelve_su_codigo(tmp_path):
    def sin_cuenta():
        raise OAuthError("La cuenta de YouTube no está conectada.", http_status=409)

    publicador = YouTubePublisher(
        content_provider=lambda content_id: _contenido(tmp_path),
        credentials_provider=sin_cuenta,
        service_factory=lambda credenciales: _Servicio(_Peticion([])),
    )

    resultado = publicador.publish_video("c-001")

    assert resultado.success is False
    assert resultado.error_code is ErrorCode.OAUTH_ERROR


# --- Mapeo de errores de la API (lo afina US-C3.6) --------------------------


@pytest.mark.parametrize(
    ("status", "reason", "esperado"),
    [
        (403, "quotaExceeded", ErrorCode.QUOTA_EXCEEDED),
        (401, "authError", ErrorCode.OAUTH_ERROR),
        (403, "insufficientPermissions", ErrorCode.OAUTH_ERROR),
        (500, "backendError", ErrorCode.PUBLISH_FAILED),
        (503, "backendError", ErrorCode.PUBLISH_FAILED),
        (400, "invalidMetadata", ErrorCode.INVALID_METADATA),
        (404, "videoNotFound", ErrorCode.CONTENT_NOT_FOUND),
    ],
)
def test_errores_http_se_traducen_a_codigos_registrados(tmp_path, status, reason, esperado):
    publicador, _ = _publicador(tmp_path, _PeticionQueFalla(_http_error(status, reason)))

    resultado = publicador.publish_video("c-001")

    assert resultado.success is False
    assert resultado.error_code is esperado


# --- Catálogo de eventos ----------------------------------------------------


def test_publish_completed_lleva_el_id_que_devolvio_youtube(session, events, tmp_path):
    publicacion = schedule_publication(
        session,
        content_id="c-yt",
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        tz_name="America/Santiago",
    )
    events.clear()

    peticion = _Peticion([(None, {"id": VIDEO_ID})])
    publicador, _ = _publicador(tmp_path, peticion)
    set_publisher(publicador)
    try:
        execute_publication(publicacion.id)
    finally:
        set_publisher(None)

    session.expire_all()
    fila = session.get(Publication, publicacion.id)
    assert fila.state == "published"
    assert fila.youtube_video_id == VIDEO_ID

    completados = events.events_of_type("publish.completed")
    assert len(completados) == 1
    payload = completados[0]["payload"]
    # Campos exactos del catálogo (docs/contratos/eventos.md).
    assert set(payload) == {"contentId", "youtubeVideoId", "publishedAt"}
    assert payload["youtubeVideoId"] == VIDEO_ID


# --- Provider local de desarrollo (sin M1) ----------------------------------


def test_content_from_local_file_busca_el_content_id(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "YOUTUBE_VIDEO_DIR", str(tmp_path))
    (tmp_path / "c-777.mp4").write_bytes(b"video")

    contenido = content_from_local_file("c-777")

    assert contenido.title == "c-777"
    assert contenido.file_path == tmp_path / "c-777.mp4"


def test_content_from_local_file_sin_archivo_es_content_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "YOUTUBE_VIDEO_DIR", str(tmp_path))

    with pytest.raises(DomainError) as exc:
        content_from_local_file("c-000")

    assert exc.value.code is ErrorCode.CONTENT_NOT_FOUND


def test_content_id_con_separadores_de_ruta_se_rechaza():
    with pytest.raises(DomainError) as exc:
        content_from_local_file("../otro")

    assert exc.value.code is ErrorCode.INVALID_METADATA
