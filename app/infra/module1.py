"""US-C3.4 — Lectura de metadatos del Módulo 1 (Gestión de Contenidos).

M3 no comparte base de datos con M1. Para publicar necesita los metadatos
versionados que administra M1; este cliente consulta el endpoint REST acordado:

    GET {MODULE1_BASE_URL}/api/content/{contentId}/metadata

La respuesta real de M1 es::

    {
      "status": 200,
      "contentId": "uuid",
      "metadata": {
        "id": "uuid-de-la-version",
        "version": 1,
        "title": "...",
        "description": "...",
        "tags": ["..."],
        "visibility": "public" | "private" | "unlisted",
        "createdAt": "..."
      }
    }

El contrato y el punto pendiente del video están documentados en
`docs/contratos/rest.md` (sección «Integración saliente con Módulo 1»).

Se usa `urllib` de la librería estándar a propósito: M3 no necesita otra
dependencia de runtime para una única llamada GET.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from app.config import get_settings
from app.domain.errors import DomainError, ErrorCode

_VISIBILIDADES = frozenset({"public", "private", "unlisted"})


@dataclass(frozen=True)
class ContentMetadata:
    """Metadatos que M3 necesita para construir el `videos.insert`."""

    content_id: str
    version: int
    title: str
    description: str
    tags: tuple[str, ...]
    visibility: str


def get_content_metadata(
    content_id: str,
    *,
    base_url: str | None = None,
    timeout: float | None = None,
) -> ContentMetadata:
    """Obtiene la versión vigente de metadatos de M1.

    Lanza `DomainError` con un código registrado para que el publicador lo
    convierta en `PublishResult.failure` (la política de reintentos es US-C5).
    """
    _validar_content_id(content_id)

    settings = get_settings()
    base = (base_url if base_url is not None else settings.MODULE1_BASE_URL).rstrip("/")
    if not base:
        raise DomainError(
            ErrorCode.PUBLISH_FAILED,
            "MODULE1_BASE_URL no está configurada: no se pueden obtener los "
            "metadatos del Módulo 1.",
        )

    url = f"{base}/api/content/{quote(content_id, safe='')}/metadata"
    peticion = Request(
        url,
        headers={
            "Accept": "application/json",
            # El contrato de M1 pide esta cabecera en el endpoint de metadatos.
            # El publicador no recibe el correlationId de la publicación, así
            # que se genera uno para esta consulta de lectura.
            "X-Correlation-Id": str(uuid4()),
        },
        method="GET",
    )

    try:
        with urlopen(
            peticion,
            timeout=timeout if timeout is not None else settings.MODULE1_TIMEOUT_SECONDS,
        ) as respuesta:
            cuerpo = respuesta.read()
    except HTTPError as exc:
        raise _error_http(exc, content_id) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise DomainError(
            ErrorCode.PUBLISH_FAILED,
            f"No se pudo consultar los metadatos de {content_id} en el Módulo 1: {exc}",
        ) from exc

    return _parsear(cuerpo, content_id)


def _validar_content_id(content_id: str) -> None:
    if not content_id or content_id in {".", ".."}:
        raise DomainError(ErrorCode.INVALID_METADATA, "contentId vacío o inválido.")
    if any(separador in content_id for separador in ("/", "\\", "\x00")):
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"contentId inválido: {content_id!r} no puede contener separadores de ruta.",
        )


def _parsear(cuerpo: bytes, content_id: str) -> ContentMetadata:
    try:
        datos = json.loads(cuerpo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DomainError(
            ErrorCode.PUBLISH_FAILED,
            f"El Módulo 1 devolvió una respuesta ilegible para {content_id}.",
        ) from exc

    if not isinstance(datos, dict):
        raise DomainError(
            ErrorCode.PUBLISH_FAILED,
            f"El Módulo 1 devolvió una respuesta inesperada para {content_id}.",
        )

    content_id_respuesta = datos.get("contentId")
    if content_id_respuesta is not None and content_id_respuesta != content_id:
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"El Módulo 1 devolvió metadatos de otro contenido: "
            f"{content_id_respuesta!r} (se pidió {content_id!r}).",
        )

    metadata = datos.get("metadata")
    if metadata is None:
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"El contenido {content_id} no tiene metadatos en el Módulo 1; "
            "no se puede publicar sin título ni visibilidad.",
        )
    if not isinstance(metadata, dict):
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"El Módulo 1 devolvió metadatos con formato inválido para {content_id}.",
        )

    version = metadata.get("version")
    if not isinstance(version, int) or version < 1:
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"Los metadatos de {content_id} no traen una versión válida.",
        )

    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"Los metadatos de {content_id} no traen un título válido.",
        )

    description = metadata.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"La descripción de {content_id} no es un texto.",
        )

    tags = metadata.get("tags")
    if tags is None:
        tags = []
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"Las etiquetas de {content_id} no son una lista de textos.",
        )

    visibility = metadata.get("visibility")
    if not isinstance(visibility, str) or visibility not in _VISIBILIDADES:
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            f"La visibilidad de {content_id} es inválida: {visibility!r}. "
            f"Valores esperados: {sorted(_VISIBILIDADES)}.",
        )

    return ContentMetadata(
        content_id=content_id,
        version=version,
        title=title.strip(),
        description=description,
        tags=tuple(tags),
        visibility=visibility,
    )


def _error_http(exc: HTTPError, content_id: str) -> DomainError:
    status = exc.code
    mensaje = _mensaje_http(exc) or str(exc.reason)
    codigo = _codigo_http(status)
    return DomainError(
        codigo,
        f"El Módulo 1 respondió {status} al pedir metadatos de {content_id}: {mensaje}",
    )


def _codigo_http(status: int) -> ErrorCode:
    if status == 404:
        return ErrorCode.CONTENT_NOT_FOUND
    if status in (400, 422):
        return ErrorCode.INVALID_METADATA
    if status in (401, 403):
        return ErrorCode.UNAUTHORIZED
    # 5xx, 409 inesperado, etc.: no es un error de datos del contenido.
    return ErrorCode.PUBLISH_FAILED


def _mensaje_http(exc: HTTPError) -> str:
    try:
        cuerpo = exc.read()
        datos = json.loads(cuerpo.decode("utf-8"))
    except (OSError, AttributeError, UnicodeDecodeError, ValueError):
        return ""
    if isinstance(datos, dict):
        return str(datos.get("message", ""))
    return ""
