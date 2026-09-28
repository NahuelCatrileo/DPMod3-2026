"""Códigos de error del contrato compartido.

IMPORTANTE: esta lista NO se amplía sin pasar por el procedimiento de la
Guía del Estudiante §5.4 (propuesta escrita, 3 días de aviso, acuerdo en la
reunión de integración). Un código no registrado es un desajuste de contrato
y pesa en el criterio de Integración (25%).

Fuente: Doc 1 §6.3 y Doc 3 §5.
"""

from enum import Enum


class ErrorCode(str, Enum):
    CONTENT_NOT_FOUND = "CONTENT_NOT_FOUND"
    INVALID_METADATA = "INVALID_METADATA"
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    OAUTH_ERROR = "OAUTH_ERROR"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    EVENT_VALIDATION_ERROR = "EVENT_VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"


class DomainError(Exception):
    """Error de negocio que se traduce a la respuesta REST estándar.

    Formato de salida (Doc 1 §6.3):
        { "status": "error", "code": "...", "message": "..." }
    """

    def __init__(self, code: ErrorCode, message: str, http_status: int = 400) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class ScheduleConflictError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.SCHEDULE_CONFLICT, message, http_status=409)


class InvalidScheduleDataError(DomainError):
    """Datos de programación mal formados (p. ej. zona horaria inexistente)."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_METADATA, message, http_status=422)


class PublicationNotFoundError(DomainError):
    def __init__(self, publish_id: str) -> None:
        super().__init__(
            ErrorCode.CONTENT_NOT_FOUND,
            f"No existe la publicación {publish_id}",
            http_status=404,
        )
