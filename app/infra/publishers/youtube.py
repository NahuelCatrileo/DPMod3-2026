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
"""

from __future__ import annotations

import json
import logging
import mimetypes
from collections.abc import Callable
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

# Razones de la API que la política de reintentos (ADR-0006) distingue.
_QUOTA_REASONS = frozenset(
    {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "uploadLimitExceeded"}
)
_AUTH_REASONS = frozenset({"authError", "unauthorized", "invalid_grant", "insufficientPermissions"})


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
            return self._http_failure(exc, content_id)
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

    @staticmethod
    def _http_failure(exc: HttpError, content_id: str) -> PublishResult:
        status = getattr(getattr(exc, "resp", None), "status", None)
        reason = _http_error_reason(exc)
        mensaje = _http_error_message(exc) or str(exc)
        codigo = _error_code_for(status, reason)
        logger.warning(
            "youtube_publish_http_error content_id=%s status=%s reason=%s error_code=%s",
            content_id,
            status,
            reason or "-",
            codigo.value,
        )
        return PublishResult.failure(
            codigo,
            f"YouTube rechazó la subida ({status} {reason or 'sin detalle'}): {mensaje}",
        )


def _error_code_for(status: int | None, reason: str) -> ErrorCode:
    """Mapea la respuesta de la API a los códigos registrados (US-C3.6)."""
    if reason in _QUOTA_REASONS or status == 429:
        return ErrorCode.QUOTA_EXCEEDED
    if reason in _AUTH_REASONS or status in (401, 403):
        return ErrorCode.OAUTH_ERROR
    if status == 404:
        return ErrorCode.CONTENT_NOT_FOUND
    if status == 400:
        return ErrorCode.INVALID_METADATA
    # 5xx, timeout del servidor y códigos inesperados: transitorio.
    return ErrorCode.PUBLISH_FAILED


def _http_error_payload(exc: HttpError) -> dict[str, Any]:
    """Cuerpo JSON del error de Google, o {} si no se puede interpretar."""
    contenido = getattr(exc, "content", b"") or b""
    try:
        datos = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    error = datos.get("error") if isinstance(datos, dict) else None
    return error if isinstance(error, dict) else {}


def _http_error_reason(exc: HttpError) -> str:
    errores = _http_error_payload(exc).get("errors")
    if isinstance(errores, list) and errores and isinstance(errores[0], dict):
        return str(errores[0].get("reason", ""))
    return str(_http_error_payload(exc).get("status", ""))


def _http_error_message(exc: HttpError) -> str:
    return str(_http_error_payload(exc).get("message", ""))
