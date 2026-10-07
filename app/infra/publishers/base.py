"""US-C4 · Subtarea 4.1 — Interfaz base del publicador.

DECISIÓN DE DISEÑO (importante, difiere del desglose original):
El publicador NO emite eventos al broker. Devuelve un PublishResult y quien
emite `publish.completed` / `publish.failed` es el servicio de aplicación
(app/services/publishing.py).

Motivo: el criterio de aceptación de US-C4 dice "el mock emite los mismos
eventos que el real". Si cada publicador emitiera por su cuenta habría dos
caminos de emisión que se pueden desincronizar, y en Sprint 3 (US-C3)
tendríamos que reimplementar la emisión dentro del publicador real.
Con esta separación el camino de eventos es UNO SOLO y el criterio se cumple
por construcción, no por disciplina.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.classification import FailureKind
from app.domain.errors import ErrorCode


@dataclass(frozen=True)
class PublishResult:
    """Resultado de un intento de publicación.

    Satisface `ClassifiableResult` (app/domain/classification.py), que es la
    vista mínima que consume `classify` para decidir la categoría del fallo.
    """

    success: bool
    youtube_video_id: str | None = None
    error_code: ErrorCode | None = None
    reason: str | None = None
    # US-C5.1: naturaleza del fallo por debajo del código, necesaria porque
    # PUBLISH_FAILED es genérico. Campo opcional y al final a propósito: no
    # rompe a quien construye el resultado con los cuatro campos de US-C4, y no
    # viaja en `publish.failed`, así que el contrato de eventos no cambia.
    failure_kind: FailureKind | None = None

    @classmethod
    def ok(cls, youtube_video_id: str) -> PublishResult:
        return cls(success=True, youtube_video_id=youtube_video_id)

    @classmethod
    def failure(
        cls,
        error_code: ErrorCode,
        reason: str,
        failure_kind: FailureKind | None = None,
    ) -> PublishResult:
        return cls(
            success=False,
            error_code=error_code,
            reason=reason,
            failure_kind=failure_kind,
        )


class Publisher(ABC):
    """Contrato que deben cumplir MockPublisher y YouTubePublisher."""

    name: str = "abstract"

    @abstractmethod
    def publish_video(self, content_id: str) -> PublishResult:
        """Intenta publicar el contenido. No lanza excepciones de negocio:
        los fallos esperables se devuelven como PublishResult.failure()."""
