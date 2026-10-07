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


class ErrorClass(str, Enum):
    """US-C5 · Qué hacer con un error de publicación.

    La clase NO se expone en eventos ni en REST: es criterio interno que
    decide la política de reintentos (ADR-0006).
    """

    # Fallo pasajero de la API o de la red: se reintenta con backoff.
    TRANSITORIO = "transitorio"
    # Cuota agotada: se reintenta, pero con un delay largo (la cuota de la
    # YouTube Data API se restablece por ventana, no en segundos).
    DIFERIBLE = "diferible"
    # No se va a resolver solo (token inválido, metadatos malos): no se
    # reintenta; se marca failed y se emite publish.failed de inmediato.
    DEFINITIVO = "definitivo"


# Mapeo ErrorCode -> ErrorClass. Fuente única de la política de reintentos.
# Toda la tabla es DEFINITIVO por defecto salvo las tres excepciones de
# US-C5: transitorias, cuota y las que nunca se arreglan reintentando.
ERROR_CLASS_BY_CODE: dict[ErrorCode, ErrorClass] = {
    # Transitorios: 5xx, timeout o caída del servicio externo.
    ErrorCode.PUBLISH_FAILED: ErrorClass.TRANSITORIO,
    ErrorCode.EVENT_VALIDATION_ERROR: ErrorClass.TRANSITORIO,
    # Cuota: transitorio, pero con ventana de recuperación larga.
    ErrorCode.QUOTA_EXCEEDED: ErrorClass.DIFERIBLE,
    # Definitivos: reintentar no cambia el resultado.
    ErrorCode.OAUTH_ERROR: ErrorClass.DEFINITIVO,
    ErrorCode.UNAUTHORIZED: ErrorClass.DEFINITIVO,
    ErrorCode.INVALID_METADATA: ErrorClass.DEFINITIVO,
    ErrorCode.CONTENT_NOT_FOUND: ErrorClass.DEFINITIVO,
    ErrorCode.DUPLICATE_CONTENT: ErrorClass.DEFINITIVO,
    ErrorCode.SCHEDULE_CONFLICT: ErrorClass.DEFINITIVO,
}


def classify(code: ErrorCode) -> ErrorClass:
    """Clasifica un ErrorCode para la política de reintento (US-C5).

    Si el catálogo se ampliara sin actualizar esta tabla, el código se trata
    como DEFINITIVO: no se reintenta lo que no se entiende. Un test
    exhaustivo sobre ErrorCode mantiene la tabla completa.
    """
    return ERROR_CLASS_BY_CODE.get(code, ErrorClass.DEFINITIVO)


class DomainError(Exception):
    """Error de negocio que se traduce a la respuesta REST estándar.

    Formato de salida (Doc 1 §6.3):
        { "status": "error", "code": "...", "message": "..." }
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        http_status: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.headers = headers
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


class UnauthorizedError(DomainError):
    """Falta el token JWT o no es válido (ADR-0005)."""

    def __init__(self, message: str) -> None:
        super().__init__(
            ErrorCode.UNAUTHORIZED,
            message,
            http_status=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
