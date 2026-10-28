"""US-C3.6 · Mapeo de errores de la API + US-C3.7 · Pruebas.

US-C3.6 — La YouTube Data API rechaza con un status HTTP y un `reason` propio.
`map_http_error()` los traduce a un `ErrorCode` de la lista registrada (Doc 1
§6.3) y de ahí, sin tabla nueva, se resuelve la clase de reintento de US-C5
(ADR-0006). La tabla vive en `app/infra/publishers/youtube.py` y está explicada
en ADR-0008.

US-C3.7 — Tres niveles de prueba:

1. **Unitarias con HttpMock**: `googleapiclient.http.HttpMockSequence` con un
   `build()` real, así el `HttpError` que se ejercita lo construye la propia
   librería a partir de una respuesta HTTP y no un objeto armado a mano.
2. **Contrato del evento**: `publish.completed` se valida contra
   `docs/contratos/eventos.md` (sobre y payload exactos, tipos, formato ISO-8601
   en UTC, `correlationId` heredado de la publicación).
3. **Manual contra un canal real**: opt-in con `YOUTUBE_REAL_CHANNEL_TEST=1`.
   Se salta sola en CI y en cualquier máquina sin credenciales.
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence
from httplib2 import Response

from app.domain.errors import ErrorClass, ErrorCode, classify
from app.infra.models import Publication
from app.infra.publishers.base import PublishResult
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.youtube import (
    VideoContent,
    YouTubePublisher,
    content_from_local_file,
    error_code_for_failure,
    http_failure_signals,
    map_http_error,
)
from app.services.publishing import execute_publication
from app.services.retry import should_retry
from app.services.scheduling import schedule_publication

VIDEO_ID = "dQw4w9WgXcQ"
# `timestamp` y `publishedAt` del contrato: UTC con sufijo Z y sin fracciones.
_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Sobre y payload acordados (docs/contratos/eventos.md). Si alguien agrega un
# campo sin pasar por el procedimiento §5.4, estas pruebas fallan.
_ENVELOPE_KEYS = {
    "id",
    "type",
    "version",
    "timestamp",
    "correlationId",
    "causationId",
    "source",
    "payload",
}
_PAYLOAD_KEYS = {"contentId", "youtubeVideoId", "publishedAt"}


# --- Constructores de HttpError --------------------------------------------


def _cuerpo(status, reasons=(), *, message="fallo de prueba", status_field=None, details=()):
    """Cuerpo de error con la forma real de la YouTube Data API.

    Con `status=None` se omite `code`, que es justo el caso de una respuesta
    que no trae ni status HTTP ni código en el cuerpo.
    """
    error: dict = {"message": message}
    if status is not None:
        error["code"] = status
    if reasons:
        error["errors"] = [
            {"domain": "youtube.api", "reason": r, "message": message} for r in reasons
        ]
    if details:
        error["details"] = [{"reason": r} for r in details]
    if status_field is not None:
        error["status"] = status_field
    return {"error": error}


# `HttpError` exige un `resp` al construirse (usa `resp.reason`), así que para
# simular una respuesta sin status se pasa un objeto con `status=None`.
_RESP_SIN_STATUS = SimpleNamespace(status=None, reason="")


def _http_error(status, reasons=(), *, cuerpo=None, raw=None, con_resp=True, **kwargs):
    """`HttpError` con el cuerpo indicado.

    `con_resp=False` deja la respuesta sin `status` HTTP, que es el caso en que
    el código solo puede salir del cuerpo.
    """
    if raw is not None:
        contenido = raw
    else:
        contenido = json.dumps(
            cuerpo if cuerpo is not None else _cuerpo(status, reasons, **kwargs)
        ).encode("utf-8")
    if con_resp and status is not None:
        respuesta = Response({"status": status})
    else:
        respuesta = _RESP_SIN_STATUS
    return HttpError(respuesta, contenido)


# --- US-C3.6 · Tabla de traducción -----------------------------------------


@pytest.mark.parametrize(
    ("status", "reasons", "esperado"),
    [
        # Cuota: DIFERIBLE (se reintenta con espera larga).
        (403, ["quotaExceeded"], ErrorCode.QUOTA_EXCEEDED),
        (403, ["dailyLimitExceeded"], ErrorCode.QUOTA_EXCEEDED),
        (403, ["rateLimitExceeded"], ErrorCode.QUOTA_EXCEEDED),
        (403, ["userRateLimitExceeded"], ErrorCode.QUOTA_EXCEEDED),
        (429, [], ErrorCode.QUOTA_EXCEEDED),
        # El reason manda sobre el status: 400 con razón de cuota es cuota.
        (400, ["uploadLimitExceeded"], ErrorCode.QUOTA_EXCEEDED),
        # Autorización: DEFINITIVO (necesita una persona).
        (401, ["authError"], ErrorCode.OAUTH_ERROR),
        (401, [], ErrorCode.OAUTH_ERROR),
        (403, ["insufficientPermissions"], ErrorCode.OAUTH_ERROR),
        (403, ["forbidden"], ErrorCode.OAUTH_ERROR),
        (403, ["invalid_grant"], ErrorCode.OAUTH_ERROR),
        # 403 sin reason reconocible: permiso, no cuota.
        (403, [], ErrorCode.OAUTH_ERROR),
        # Contenido y metadatos: DEFINITIVOS.
        (404, ["videoNotFound"], ErrorCode.CONTENT_NOT_FOUND),
        (404, [], ErrorCode.CONTENT_NOT_FOUND),
        (400, ["invalidMetadata"], ErrorCode.INVALID_METADATA),
        (400, ["invalidTitle"], ErrorCode.INVALID_METADATA),
        (400, [], ErrorCode.INVALID_METADATA),
        # Sin clasificar: TRANSITORIO (se reintenta).
        (500, ["backendError"], ErrorCode.PUBLISH_FAILED),
        (502, [], ErrorCode.PUBLISH_FAILED),
        (503, [], ErrorCode.PUBLISH_FAILED),
        (504, [], ErrorCode.PUBLISH_FAILED),
        # Status que no conocemos: transitorio a propósito.
        (409, [], ErrorCode.PUBLISH_FAILED),
    ],
)
def test_mapea_la_respuesta_al_codigo_registrado(status, reasons, esperado):
    assert map_http_error(_http_error(status, reasons)) is esperado


def test_el_reason_manda_sobre_el_status():
    """Mismo status, distinto reason: la respuesta de Google gana.

    403 es el caso que obliga a esta regla: la API lo usa para cuota agotada y
    también para permisos, y solo el `reason` los distingue.
    """
    cuota = map_http_error(_http_error(403, ["quotaExceeded"]))
    permisos = map_http_error(_http_error(403, ["insufficientPermissions"]))
    sin_reason = map_http_error(_http_error(403, []))

    assert cuota is ErrorCode.QUOTA_EXCEEDED
    assert permisos is ErrorCode.OAUTH_ERROR
    assert sin_reason is ErrorCode.OAUTH_ERROR


def test_los_codigos_mapeados_heredan_la_clase_de_reintento_de_us_c5():
    """El mapeo no define política: solo elige un código de US-C5.

    La clase y la decisión de reintentar salen de `app.domain.errors`, que es
    la fuente única de ADR-0006. Esto es lo que pide US-C3.6: traducir a los
    códigos de US-C5, no inventar una tabla paralela.
    """
    cuota = classify(map_http_error(_http_error(403, ["quotaExceeded"])))
    autorizacion = classify(map_http_error(_http_error(403, ["authError"])))
    servidor = classify(map_http_error(_http_error(500, ["backendError"])))

    assert cuota is ErrorClass.DIFERIBLE
    assert autorizacion is ErrorClass.DEFINITIVO
    assert servidor is ErrorClass.TRANSITORIO


@pytest.mark.parametrize(
    ("status", "reasons", "reintenta"),
    [
        # Transitorio: reintenta mientras queden intentos.
        (500, ["backendError"], True),
        # Diferible (cuota): también reintenta.
        (403, ["quotaExceeded"], True),
        # Definitivo: nunca. Reintentar no cambia el resultado.
        (401, ["authError"], False),
        (400, ["invalidMetadata"], False),
        (404, ["videoNotFound"], False),
    ],
)
def test_la_respuesta_decide_si_se_reintenta(status, reasons, reintenta):
    codigo = map_http_error(_http_error(status, reasons))
    clase = classify(codigo)

    assert should_retry(clase, attempts=1) is reintenta
    if reintenta:
        # Se agota al llegar al máximo de intentos.
        assert should_retry(clase, attempts=1_000) is False


# --- US-C3.6 · Robustez del parseo -----------------------------------------


def test_cuerpo_que_no_es_json_es_transitorio():
    """Un 500 con HTML (proxy, balanceador) no puede romper el publicador."""
    exc = _http_error(500, raw=b"<html>Bad Gateway</html>")

    assert map_http_error(exc) is ErrorCode.PUBLISH_FAILED
    # El mensaje cae al texto de la excepción, no revienta.
    assert http_failure_signals(exc).message


def test_cuerpo_json_sin_clave_error_es_transitorio():
    assert map_http_error(_http_error(500, cuerpo={"foo": "bar"})) is (
        ErrorCode.PUBLISH_FAILED
    )


def test_cuerpo_vacio_usa_el_status():
    assert map_http_error(_http_error(503, raw=b"")) is ErrorCode.PUBLISH_FAILED
    assert map_http_error(_http_error(403, raw=b"")) is ErrorCode.OAUTH_ERROR


def test_bytes_que_no_son_utf8_no_revientan():
    assert map_http_error(_http_error(500, raw=b"\xff\xfe\x00")) is (
        ErrorCode.PUBLISH_FAILED
    )


def test_sin_status_http_pero_con_reason_se_clasifica_por_reason():
    """Sin status HTTP, un `reason` reconocible alcanza para clasificar."""
    exc = _http_error(None, ["quotaExceeded"], con_resp=False)

    señales = http_failure_signals(exc)

    assert señales.status is None
    assert map_http_error(exc) is ErrorCode.QUOTA_EXCEEDED


def test_sin_status_ni_pistas_es_transitorio():
    exc = _http_error(None, cuerpo={"error": {"message": "sin código"}}, con_resp=False)

    assert http_failure_signals(exc).status is None
    assert map_http_error(exc) is ErrorCode.PUBLISH_FAILED


def test_usa_el_codigo_del_cuerpo_cuando_falta_el_status_http():
    exc = HttpError(
        _RESP_SIN_STATUS,
        json.dumps(_cuerpo(403, ["insufficientPermissions"])).encode(),
    )

    assert http_failure_signals(exc).status == 403
    assert map_http_error(exc) is ErrorCode.OAUTH_ERROR


def test_mira_todas_las_razones_no_solo_la_primera():
    """Google manda varias entradas en `errors`; la útil puede no ser la 1.ª."""
    exc = _http_error(
        403,
        cuerpo={
            "error": {
                "code": 403,
                "message": "fallo",
                "errors": [
                    {"reason": "otraCosa", "message": "irrelevante"},
                    {"reason": "quotaExceeded", "message": "sin cuota"},
                ],
            }
        },
    )

    señales = http_failure_signals(exc)

    assert señales.reasons == ("otraCosa", "quotaExceeded")
    assert map_http_error(exc) is ErrorCode.QUOTA_EXCEEDED


def test_lee_el_reason_de_details_y_de_error_status():
    """Google a veces solo manda el motivo en `details` o en `error.status`."""
    por_details = _http_error(400, details=["invalidVideoMetadata"])
    por_status = _http_error(403, status_field="PERMISSION_DENIED")

    assert map_http_error(por_details) is ErrorCode.INVALID_METADATA
    assert map_http_error(por_status) is ErrorCode.OAUTH_ERROR


def test_las_razones_no_se_repiten():
    exc = _http_error(
        403,
        cuerpo={
            "error": {
                "code": 403,
                "errors": [{"reason": "forbidden"}, {"reason": "forbidden"}],
                "status": "forbidden",
            }
        },
    )

    assert http_failure_signals(exc).reasons == ("forbidden",)


def test_error_code_for_failure_es_la_tabla_sin_pasar_por_http():
    """La tabla también se puede consultar sin construir un HttpError."""
    assert error_code_for_failure(429, ()) is ErrorCode.QUOTA_EXCEEDED
    assert error_code_for_failure(None, ("quotaExceeded",)) is (
        ErrorCode.QUOTA_EXCEEDED
    )
    assert error_code_for_failure(None, ()) is ErrorCode.PUBLISH_FAILED


# --- US-C3.7 · Unitarias con HttpMock (googleapiclient real) ---------------


def _contenido(tmp_path, titulo="Video de prueba"):
    archivo = tmp_path / "video.mp4"
    archivo.write_bytes(b"contenido de video")
    return VideoContent(
        title=titulo,
        file_path=archivo,
        description="Descripción",
        tags=("pubtube",),
    )


def _servicio_con_http_mock(respuestas):
    """Servicio de YouTube real cuyo transporte es `HttpMockSequence`.

    `cache_discovery=False` para que la prueba no escriba caché en disco: el
    servicio se arma con el discovery estático incluido en la librería.
    """
    return build(
        "youtube",
        "v3",
        http=HttpMockSequence(respuestas),
        static_discovery=True,
        cache_discovery=False,
    )


def _publicador_con_http_mock(tmp_path, respuestas):
    service = _servicio_con_http_mock(respuestas)
    contenido = _contenido(tmp_path)
    return YouTubePublisher(
        content_provider=lambda content_id: contenido,
        credentials_provider=lambda: object(),
        service_factory=lambda credenciales: service,
    )


def _respuesta_error(status, reasons):
    return (
        {"status": str(status)},
        json.dumps(_cuerpo(status, reasons)).encode("utf-8"),
    )


def _respuesta_inicio_subida():
    return (
        {"status": "200", "location": "https://upload.example/resumable?id=abc"},
        b"",
    )


def _respuesta_video(video_id=VIDEO_ID):
    return ({"status": "200"}, json.dumps({"id": video_id}).encode("utf-8"))


@pytest.mark.parametrize(
    ("status", "reasons", "esperado"),
    [
        (403, ["quotaExceeded"], ErrorCode.QUOTA_EXCEEDED),
        (401, ["authError"], ErrorCode.OAUTH_ERROR),
        (403, ["insufficientPermissions"], ErrorCode.OAUTH_ERROR),
        (500, ["backendError"], ErrorCode.PUBLISH_FAILED),
        (503, ["backendError"], ErrorCode.PUBLISH_FAILED),
        (400, ["invalidMetadata"], ErrorCode.INVALID_METADATA),
        (404, ["videoNotFound"], ErrorCode.CONTENT_NOT_FOUND),
    ],
)
def test_httpmock_error_al_iniciar_la_subida(tmp_path, status, reasons, esperado):
    """El `HttpError` lo construye googleapiclient a partir de la respuesta.

    Es la diferencia con un `HttpError` armado a mano: aquí se ejecuta el mismo
    camino que en producción (cliente → transporte → excepción → mapeo).
    """
    publicador = _publicador_con_http_mock(
        tmp_path, [_respuesta_error(status, reasons)]
    )

    resultado = publicador.publish_video("c-001")

    assert resultado.success is False
    assert resultado.error_code is esperado
    assert resultado.reason


def test_httpmock_error_durante_el_envio_de_bloques(tmp_path):
    """El error puede llegar después de abrir la sesión de subida."""
    publicador = _publicador_con_http_mock(
        tmp_path,
        [_respuesta_inicio_subida(), _respuesta_error(403, ["quotaExceeded"])],
    )

    resultado = publicador.publish_video("c-001")

    assert resultado.success is False
    assert resultado.error_code is ErrorCode.QUOTA_EXCEEDED


def test_httpmock_subida_exitosa_devuelve_el_id(tmp_path):
    publicador = _publicador_con_http_mock(
        tmp_path, [_respuesta_inicio_subida(), _respuesta_video()]
    )

    resultado = publicador.publish_video("c-001")

    assert resultado == PublishResult.ok(VIDEO_ID)


def test_httpmock_el_mensaje_conserva_status_y_reason(tmp_path):
    publicador = _publicador_con_http_mock(
        tmp_path, [_respuesta_error(403, ["quotaExceeded"])]
    )

    resultado = publicador.publish_video("c-001")

    assert resultado.reason is not None
    assert "403" in resultado.reason
    assert "quotaExceeded" in resultado.reason


# --- US-C3.7 · Contrato del evento publish.completed -----------------------


def test_contrato_de_publish_completed_con_subida_real_simulada(
    session, events, tmp_path
):
    """Recorre el camino completo y valida el evento contra el catálogo.

    Publicador real (sobre `HttpMockSequence`) → `execute_publication` →
    `publish.completed`. Se verifica el sobre entero y el payload campo por
    campo, no solo que exista el evento.
    """
    publicacion = schedule_publication(
        session,
        content_id="c-contrato",
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        tz_name="America/Santiago",
        correlation_id="corr-contrato-123",
    )
    events.clear()

    publicador = _publicador_con_http_mock(
        tmp_path, [_respuesta_inicio_subida(), _respuesta_video()]
    )
    set_publisher(publicador)
    try:
        execute_publication(publicacion.id)
    finally:
        set_publisher(None)

    # 1. Una sola publicación: el evento se emite una vez.
    completados = events.events_of_type("publish.completed")
    assert len(completados) == 1
    evento = completados[0]

    # 2. Sobre exacto del contrato: ni un campo de más ni de menos.
    assert set(evento) == _ENVELOPE_KEYS
    assert evento["type"] == "publish.completed"
    assert evento["version"] == 1
    assert evento["source"] == "module-3"
    assert uuid.UUID(evento["id"])
    # Sin causa previa, el evento se causa a sí mismo (eventos.md).
    assert evento["causationId"] == evento["id"]
    assert evento["correlationId"] == "corr-contrato-123"

    # 3. `timestamp` en UTC con sufijo Z, como el resto del contrato.
    assert _ISO_UTC.match(evento["timestamp"]), evento["timestamp"]
    datetime.strptime(evento["timestamp"], "%Y-%m-%dT%H:%M:%SZ")

    # 4. Payload exacto del catálogo.
    payload = evento["payload"]
    assert set(payload) == _PAYLOAD_KEYS
    assert payload["contentId"] == "c-contrato"
    assert payload["youtubeVideoId"] == VIDEO_ID
    assert _ISO_UTC.match(payload["publishedAt"]), payload["publishedAt"]
    datetime.strptime(payload["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")

    # 5. Y el estado quedó persistido, no solo emitido.
    session.expire_all()
    fila = session.get(Publication, publicacion.id)
    assert fila.state == "published"
    assert fila.youtube_video_id == VIDEO_ID


# --- US-C3.7 · Prueba manual contra un canal real --------------------------

# Se salta sola salvo que alguien la pida explícitamente. No corre en CI: no
# hay credenciales y no queremos gastar cuota. Cómo ejecutarla y qué deja
# atrás está en docs/oauth-youtube.md → «Prueba manual contra el canal real».
_MANUAL = os.environ.get("YOUTUBE_REAL_CHANNEL_TEST") == "1"


@pytest.mark.manual
@pytest.mark.skipif(
    not _MANUAL,
    reason="prueba manual: exporta YOUTUBE_REAL_CHANNEL_TEST=1 para ejecutarla",
)
def test_publicacion_real_contra_un_canal(monkeypatch):
    """Sube un video de verdad y comprueba que YouTube devuelve un id.

    Requiere: `.env` con GOOGLE_CLIENT_ID/SECRET y OAUTH_TOKEN_ENCRYPTION_KEY,
    la cuenta ya autorizada (POST /oauth2/authorize), y un video en
    `YOUTUBE_REAL_VIDEO` (por defecto `videos/real-test.mp4`).

    Deja un video **privado** en el canal: el id y el enlace al Studio se
    imprimen al final para poder borrarlo a mano. Es el precio de comprobar de
    verdad la integración; no hay forma de subir y no dejar rastro.
    """
    from pathlib import Path

    from app.config import get_settings

    ruta = Path(os.environ.get("YOUTUBE_REAL_VIDEO", "videos/real-test.mp4"))
    assert ruta.is_file(), f"falta el video de prueba: {ruta}"

    monkeypatch.setattr(get_settings(), "YOUTUBE_VIDEO_DIR", str(ruta.parent))

    publicador = YouTubePublisher(content_provider=content_from_local_file)
    resultado = publicador.publish_video(ruta.stem)

    assert resultado.success is True, f"falló la subida real: {resultado.reason}"
    assert resultado.youtube_video_id, "YouTube no devolvió el id del video"
    print(
        "Video subido (privado). Bórralo en "
        f"https://studio.youtube.com/video/{resultado.youtube_video_id}/edit"
    )
