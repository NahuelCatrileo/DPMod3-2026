"""Envelope estándar de eventos.

Guía Técnica §3.1:
    { id, type, version, timestamp, correlationId, causationId, source, payload }

OJO — punto abierto de contrato:
El Doc 3 §3.1 incluye "source": "module-3" y el modelo event_store del M2
(Doc 3 §6) tiene columna `source`. El Doc 1 §6.3 lo omite, pero ese ejemplo
está abreviado. Como el Doc 1 tiene precedencia sobre el Doc 3 (Guía §1.2),
es una contradicción real entre documentos.

ACCIÓN PENDIENTE: confirmarlo con Equipo B en la reunión de integración y
dejarlo en acta. Si ellos validan con additionalProperties:false y nosotros
mandamos `source`, el evento se va a la DLQ.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings

ENVELOPE_VERSION = 1


def build_envelope(
    *,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str,
    causation_id: str | None = None,
    version: int = ENVELOPE_VERSION,
) -> dict[str, Any]:
    """Arma el sobre estándar. No publica nada: solo construye el dict."""
    settings = get_settings()
    event_id = str(uuid.uuid4())
    return {
        "id": event_id,
        "type": event_type,
        "version": version,
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "correlationId": correlation_id,
        # Si el evento no fue causado por otro, se causa a sí mismo.
        "causationId": causation_id or event_id,
        "source": settings.MODULE_NAME,
        "payload": payload,
    }


# --- Payloads del catálogo (Doc 1 §6.1 / Doc 3 §3.3) -----------------------
# Los campos son EXACTAMENTE los acordados. Agregar uno requiere §5.4.


def payload_publish_scheduled(
    *, content_id: str, schedule_at_iso: str, tz_name: str
) -> dict[str, Any]:
    return {
        "contentId": content_id,
        "scheduleAt": schedule_at_iso,
        "timezone": tz_name,
    }


def payload_publish_completed(
    *, content_id: str, youtube_video_id: str, published_at_iso: str
) -> dict[str, Any]:
    return {
        "contentId": content_id,
        "youtubeVideoId": youtube_video_id,
        "publishedAt": published_at_iso,
    }


def payload_publish_failed(
    *, content_id: str, error_code: str, attempt: int, reason: str
) -> dict[str, Any]:
    return {
        "contentId": content_id,
        "errorCode": error_code,
        "attempt": attempt,
        "reason": reason,
    }
