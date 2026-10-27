"""US-C3.5 — Publicador real contra la YouTube Data API v3.

La subida usa `videos.insert` con **subida reanudable**: el video se envía por
bloques y cada respuesta trae el progreso, así un corte en un bloque no obliga
a empezar de cero. El publicador devuelve el `id` que asigna YouTube en
`PublishResult.youtube_video_id`; `services/publishing.py` es quien lo guarda y
lo pone en `publish.completed` (campo `youtubeVideoId` del catálogo).

Igual que el mock, este publicador NO emite eventos: el camino de eventos es
uno solo y vive en `services/publishing.py` (ver publishers/base.py).

US-C3.4: los metadatos salen del Módulo 1 con
`GET /api/content/{contentId}/metadata` (ver `app/infra/module1.py`). El
archivo del video sigue siendo provisional: M1 todavía no expone una descarga
ni una URL prefirmada, así que `content_from_m1` busca
`<YOUTUBE_VIDEO_DIR>/<contentId>.<ext>` y falla con `CONTENT_NOT_FOUND` si no
está. Cuando M1 entregue el video, solo se reemplaza la búsqueda local; el
mapeo de metadatos y la subida no cambian.

La costura es el primer argumento del constructor: se puede inyectar un
`content_provider` que devuelva el mismo `VideoContent` (lo hacen los tests y
lo hará la integración cuando cambie el origen del archivo).

US-C3.6: la YouTube Data API rechaza con HTTP + un `reason` propio, no con
nuestros códigos. `map_http_error()` traduce esa respuesta a un `ErrorCode` de
la lista registrada; de ahí la clase de reintento (US-C5, ADR-0006) se resuelve
sola. La tabla y sus reglas están documentadas en el bloque de constantes de
abajo y en `ADR/0008-oauth-youtube-consentimiento-tokens.md`.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from app.config import get_settings
from app.domain.errors import DomainError, ErrorCode
from app.infra.module1 import get_content_metadata
from app.infra.publishers.base import Publisher, PublishResult
from app.oauth.credentials import get_credentials
from app.oauth.youtube_client import create_youtube_service

logger = logging.getLogger(__name__)

# 5 MiB: múltiplo de 256 KiB, como exige la subida reanudable de YouTube.
_UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
# 22 = People & Blogs. YouTube pide un categoryId en snippet.
_DEFAULT_CATEGORY_ID = "22"
# ADR-0008: lo subido desde un proyecto de API no auditado queda en privado;
# pedir "public" no cambiaría el resultado, así que el default es honesto.
_DEFAULT_PRIVACY_STATUS = "private"

# --- US-C3.6 · Traducción de HttpError a los códigos de US-C5 --------------
#
# La YouTube Data API no responde con nuestros códigos: responde un status HTTP
# y un `reason` propio. Esta tabla traduce (status, reason) a un ErrorCode
# registrado (Doc 1 §6.3) y de ahí, sin código extra, la política de reintentos
# de US-C5 decide qué hacer (ADR-0006):
#
#   QUOTA_EXCEEDED                       -> DIFERIBLE   (reintenta, espera larga)
#   OAUTH_ERROR                          -> DEFINITIVO  (necesita una persona)
#   CONTENT_NOT_FOUND / INVALID_METADATA -> DEFINITIVO  (no se arregla sola)
#   PUBLISH_FAILED                       -> TRANSITORIO (reintenta con backoff)
#
# Dos reglas gobiernan la tabla:
#
# 1. **El `reason` manda sobre el status.** Google usa 403 tanto para cuota
#    agotada como para permisos, y 400 para varios rechazos de metadatos: el
#    status solo no alcanza para distinguirlos.
# 2. **Lo desconocido es transitorio.** Si no se puede leer la respuesta, el
#    código es PUBLISH_FAILED y la publicación se reintenta: es preferible
#    gastar un intento a dar por perdida una subida por una respuesta que no
#    supimos interpretar. Un 5xx, un timeout o una respuesta sin `reason`
#    caen aquí.
#
# La traducción NO se hace en `app/domain/`: el dominio no puede importar
# googleapiclient. El dominio define los códigos y su clase; infra traduce.
_QUOTA_REASONS = frozenset(
    {
        "quotaExceeded",
        "dailyLimitExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "uploadLimitExceeded",
    }
)
_AUTH_REASONS = frozenset(
    {
        "authError",
        "unauthorized",
        "invalid_grant",
        "invalidCredentials",
        "insufficientPermissions",
        "forbidden",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
    }
)
_CONTENT_REASONS = frozenset({"videoNotFound", "notFound"})
_METADATA_REASONS = frozenset(
    {
        "invalidMetadata",
        "invalidVideoMetadata",
        "invalidFilename",
        "invalidTitle",
        "invalidDescription",
        "invalidTags",
        "invalidCategoryId",
        "invalidDefaultLanguage",
    }
)


@dataclass(frozen=True)
class VideoContent:
    """Lo que hace falta para subir un video: metadatos y archivo local."""

    title: str
    file_path: Path
    description: str = ""
    tags: tuple[str, ...] = ()
    category_id: str = _DEFAULT_CATEGORY_ID
    privacy_status: str = _DEFAULT_PRIVACY_STATUS
    # Si es None se deduce de la extensión del archivo.
    mime_type: str | None = None


class ContentProvider(Protocol):
    """Obtiene el contenido a publicar: metadatos reales + archivo local."""

    def __call__(self, content_id: str) -> VideoContent: ...


def content_from_m1(content_id: str) -> VideoContent:
    """Obtiene los metadatos del M1 y el archivo local provisional.

    La parte de metadatos de US-C3.4 ya es real: se consulta
    `GET /api/content/{contentId}/metadata`. El archivo sigue pendiente porque
    el contrato del M1 todavía no expone descarga ni URL prefirmada; se busca
    en `YOUTUBE_VIDEO_DIR` con el mismo nombre que usaba C3.5.
    """
    metadata = get_content_metadata(content_id)
    return VideoContent(
        title=metadata.title,
        file_path=_local_video_path(content_id),
        description=metadata.description,
        tags=metadata.tags,
        privacy_status=metadata.visibility,
    )


def content_from_local_file(content_id: str) -> VideoContent:
    """Proveedor de desarrollo sin M1: título = contentId y archivo local.

    Se conserva para pruebas y para trabajar sin el Módulo 1 levantado. El
    publicador real usa `content_from_m1`; este provider no consulta la red.
    """
    return VideoContent(title=content_id, file_path=_local_video_path(content_id))


def _local_video_path(content_id: str) -> Path:
    _validar_content_id(content_id)
    base = Path(get_settings().YOUTUBE_VIDEO_DIR)
    for extension in _VIDEO_EXTENSIONS:
        candidate = base / f"{content_id}{extension}"
        if candidate.is_file():
            return candidate

    raise DomainError(
        ErrorCode.CONTENT_NOT_FOUND,
        f"Se obtuvieron los metadatos de {content_id}, pero no se encontró el "
        f"video en {base} (se esperaba {content_id}.mp4, .mov, .mkv, .webm, "
        ".avi o .m4v). El M1 todavía no entrega el archivo: es el pendiente de "
        "US-C3.4 documentado en docs/contratos/rest.md.",
    )


def _validar_content_id(content_id: str) -> None:
    if not content_id or content_id in {".", ".."}:
        raise DomainError(ErrorCode.INVALID_METADATA, "contentId vacío o inválido.")
    if any(separador in content_id for separador in ("/", "\\", "\x00")):
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"contentId inválido: {content_id!r} no puede contener separadores de ruta.",
        )


class YouTubePublisher(Publisher):
    """Sube el video con `videos.insert` y devuelve el id de YouTube."""

    name = "youtube"

    def __init__(
        self,
        *,
        content_provider: ContentProvider | None = None,
        credentials_provider: Callable[[], Any] | None = None,
        service_factory: Callable[[Any], Any] | None = None,
        chunk_size_bytes: int = _UPLOAD_CHUNK_BYTES,
    ) -> None:
        self._content_provider = content_provider or content_from_m1
        self._credentials_provider = credentials_provider or get_credentials
        self._service_factory = service_factory or create_youtube_service
        self._chunk_size_bytes = chunk_size_bytes

    def publish_video(self, content_id: str) -> PublishResult:
        logger.info("youtube_publish_inicio content_id=%s", content_id)

        try:
            contenido = self._content_provider(content_id)
        except DomainError as exc:
            logger.warning(
                "youtube_publish_contenido_invalido content_id=%s error_code=%s motivo=%s",
                content_id,
                exc.code.value,
                exc.message,
            )
            return PublishResult.failure(exc.code, exc.message)

        if not contenido.file_path.is_file():
            logger.warning(
                "youtube_publish_archivo_inexistente content_id=%s path=%s",
                content_id,
                contenido.file_path,
            )
            return PublishResult.failure(
                ErrorCode.CONTENT_NOT_FOUND,
                f"No existe el archivo del video: {contenido.file_path}",
            )

        try:
            credenciales = self._credentials_provider()
            servicio = self._service_factory(credenciales)
            peticion = servicio.videos().insert(
                part="snippet,status",
                body=self._video_body(contenido),
                media_body=MediaFileUpload(
                    str(contenido.file_path),
                    mimetype=self._mimetype(contenido),
                    chunksize=self._chunk_size_bytes,
                    resumable=True,
                ),
            )
            respuesta = self._subir(peticion, content_id)
        except DomainError as exc:
            # get_credentials() ya traduce los fallos de OAuth a la política de
            # ADR-0006 (OAUTH_ERROR definitivo, PUBLISH_FAILED transitorio).
            logger.warning(
                "youtube_publish_credenciales_fallidas content_id=%s error_code=%s motivo=%s",
                content_id,
                exc.code.value,
                exc.message,
            )
            return PublishResult.failure(exc.code, exc.message)
        except HttpError as exc:
            return _http_failure(exc, content_id)
        except FileNotFoundError as exc:
            logger.warning(
                "youtube_publish_archivo_inexistente content_id=%s motivo=%s",
                content_id,
                exc,
            )
            return PublishResult.failure(
                ErrorCode.CONTENT_NOT_FOUND, f"No se pudo abrir el archivo del video: {exc}"
            )
        except (TransportError, TimeoutError, OSError) as exc:
            # google.auth.exceptions.TransportError no hereda de OSError; los
            # cortes de socket sí. En los dos casos es transitorio (ADR-0006).
            logger.warning(
                "youtube_publish_sin_respuesta content_id=%s motivo=%s",
                content_id,
                type(exc).__name__,
            )
            return PublishResult.failure(
                ErrorCode.PUBLISH_FAILED, f"No se pudo completar la subida a YouTube: {exc}"
            )

        video_id = respuesta.get("id") if isinstance(respuesta, dict) else None
        if not video_id:
            logger.error("youtube_publish_sin_id content_id=%s respuesta=%s", content_id, respuesta)
            return PublishResult.failure(
                ErrorCode.PUBLISH_FAILED,
                "YouTube aceptó la subida pero la respuesta no trae el id del video.",
            )

        logger.info("youtube_publish_ok content_id=%s youtube_video_id=%s", content_id, video_id)
        return PublishResult.ok(str(video_id))

    @staticmethod
    def _video_body(contenido: VideoContent) -> dict[str, Any]:
        return {
            "snippet": {
                "title": contenido.title,
                "description": contenido.description,
                "tags": list(contenido.tags),
                "categoryId": contenido.category_id,
            },
            "status": {"privacyStatus": contenido.privacy_status},
        }

    @staticmethod
    def _mimetype(contenido: VideoContent) -> str:
        return contenido.mime_type or mimetypes.guess_type(contenido.file_path.name)[0] or "video/*"

    @staticmethod
    def _subir(peticion: Any, content_id: str) -> dict[str, Any]:
        """Consume la subida reanudable hasta que YouTube devuelve el recurso."""
        respuesta = None
        while respuesta is None:
            progreso, respuesta = peticion.next_chunk()
            if progreso is not None:
                logger.info(
                    "youtube_upload_progreso content_id=%s porcentaje=%s",
                    content_id,
                    int(progreso.progress() * 100),
                )
        return respuesta


def _http_failure(exc: HttpError, content_id: str) -> PublishResult:
    señales = http_failure_signals(exc)
    codigo = map_http_error(exc)
    logger.warning(
        "youtube_publish_http_error content_id=%s status=%s reasons=%s error_code=%s",
        content_id,
        señales.status,
        ",".join(señales.reasons) or "-",
        codigo.value,
    )
    detalle = ", ".join(señales.reasons) or "sin detalle"
    return PublishResult.failure(
        codigo,
        f"YouTube rechazó la subida ({señales.status} {detalle}): {señales.message}",
    )


@dataclass(frozen=True)
class HttpFailure:
    """Lo que se pudo leer de un `HttpError`, ya normalizado."""

    status: int | None
    reasons: tuple[str, ...]
    message: str


def http_failure_signals(exc: HttpError) -> HttpFailure:
    """Extrae status, `reason`s y mensaje del cuerpo de error de Google.

    Junta los `reason` de `error.errors[]`, los de `error.details[]` y el
    `error.status` (Google a veces solo manda `PERMISSION_DENIED` ahí). Cualquier
    forma inesperada del cuerpo se ignora: quien llama decide por status.
    """
    cuerpo = _http_error_payload(exc)

    reasons: list[str] = []
    for coleccion in ("errors", "details"):
        entradas = cuerpo.get(coleccion)
        if isinstance(entradas, list):
            for entrada in entradas:
                if isinstance(entrada, dict):
                    _agregar_reason(reasons, entrada.get("reason"))
    # `error.status` viene como enum de Google: PERMISSION_DENIED, INVALID_ARGUMENT...
    _agregar_reason(reasons, cuerpo.get("status"))

    return HttpFailure(
        status=_http_status(exc, cuerpo),
        reasons=tuple(dict.fromkeys(reasons)),
        message=str(cuerpo.get("message") or "").strip() or str(exc),
    )


def map_http_error(exc: HttpError) -> ErrorCode:
    """Traduce un `HttpError` de YouTube a un código de la lista registrada.

    Es el punto que pide US-C3.6: de acá sale un `ErrorCode`, y la clase de
    reintento la decide `app.domain.errors.classify` sin tabla nueva.
    """
    señales = http_failure_signals(exc)
    return error_code_for_failure(señales.status, señales.reasons)


def error_code_for_failure(status: int | None, reasons: Iterable[str]) -> ErrorCode:
    """Tabla (status, reasons) -> ErrorCode. Ver el comentario de arriba."""
    conjunto = set(reasons)

    if conjunto & _QUOTA_REASONS or status == 429:
        return ErrorCode.QUOTA_EXCEEDED
    if conjunto & _AUTH_REASONS or status == 401:
        return ErrorCode.OAUTH_ERROR
    if conjunto & _CONTENT_REASONS or status == 404:
        return ErrorCode.CONTENT_NOT_FOUND
    if conjunto & _METADATA_REASONS or status == 400:
        return ErrorCode.INVALID_METADATA
    # 403 sin reason reconocible: permiso, no cuota (la cuota ya se filtró).
    if status == 403:
        return ErrorCode.OAUTH_ERROR
    # 5xx, status ausente y todo lo que no supimos leer: transitorio.
    return ErrorCode.PUBLISH_FAILED


def _agregar_reason(destino: list[str], valor: object) -> None:
    if isinstance(valor, str):
        limpio = valor.strip()
        if limpio:
            destino.append(limpio)


def _http_status(exc: HttpError, cuerpo: dict[str, Any]) -> int | None:
    """Status HTTP de la respuesta, o el `error.code` del cuerpo como respaldo.

    Un `HttpError` siempre trae `resp` (la clase lo exige al construirse), pero
    el `status` puede faltar. En ese caso el cuerpo suele traer el código.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = cuerpo.get("code")
    try:
        return int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _http_error_payload(exc: HttpError) -> dict[str, Any]:
    """Cuerpo JSON del error de Google, o {} si no se puede interpretar."""
    contenido = getattr(exc, "content", b"") or b""
    if isinstance(contenido, bytes | bytearray):
        try:
            contenido = contenido.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not isinstance(contenido, str):
        return {}
    try:
        datos = json.loads(contenido)
    except ValueError:
        return {}
    if not isinstance(datos, dict):
        return {}
    error = datos.get("error")
    return error if isinstance(error, dict) else {}
