"""US-C5.1 — Clasificación de errores: transitorio, diferible o definitivo.

La función `classify` responde UNA pregunta: ¿qué clase de fallo es este
resultado del publicador? No responde qué hacer con él. Los reintentos, el
backoff y el scheduler de reintentos son US-C5.3 y no viven aquí: esta historia
solo deja la tabla de clasificación sobre la que esa política va a decidir.

Propiedades exigidas a `classify` (US-C5.1 §6):

- es pura: no hace I/O, no toca la base de datos, no sale a la red, no depende
  de estado global mutable, no emite eventos y no ejecuta reintentos;
- no modifica el resultado que recibe (que además es inmutable: `@dataclass
  frozen=True`);
- es total: para cualquier código del contrato o fuera de él devuelve una de
  las tres categorías, nunca `None` ni una excepción.

Por qué vive en el dominio y no en el publicador ni en el servicio: la categoría
de un fallo es una regla de negocio estable, no un detalle de transporte. El
publicador describe lo que pasó (código + naturaleza del fallo); la categoría se
deriva siempre de la misma tabla, sin importar si detrás hay un MockPublisher o
la YouTube Data API v3.

Por qué no importa `PublishResult`: el dominio no depende de la infraestructura.
Declaramos aquí la vista mínima que la regla necesita (`ClassifiableResult`) y
`PublishResult` (app/infra/publishers/base.py) la satisface por estructura.
"""

from enum import Enum
from typing import Protocol

from app.domain.errors import ErrorCode


class ErrorCategory(str, Enum):
    """Qué clase de fallo es. Tres categorías, ni una más (US-C5.1 §3).

    Se hereda de `str` por la misma razón que en `ErrorCode` y `PublishState`:
    serializa directo a JSON en logs y evidencia sin conversión aparte.
    """

    # El problema puede desaparecer solo: se puede reintentar pronto.
    TRANSIENT = "transient"
    # Hay que esperar a que se renueve la cuota: se reintenta más tarde.
    DEFERRABLE = "deferrable"
    # Reintentar no resuelve nada: no se reintenta.
    PERMANENT = "permanent"


class FailureKind(str, Enum):
    """Naturaleza del fallo por debajo del código del contrato.

    Hace falta porque `PUBLISH_FAILED` es un código genérico: el mismo código
    cubre un timeout de red (recuperable) y una validación rechazada (no
    recuperable). El código, solo, no alcanza para decidir. Lo rellena el
    publicador; el contrato de eventos NO cambia, porque este dato no viaja en
    `publish.failed` (Doc 1 §6.3).

    Valores en minúscula, como `PublishState`: es un descriptor interno, no un
    código del catálogo compartido.
    """

    NETWORK = "network"
    TIMEOUT = "timeout"
    HTTP_5XX = "http_5xx"
    HTTP_4XX = "http_4xx"


class ClassifiableResult(Protocol):
    """Vista mínima de un resultado del publicador que el dominio necesita.

    `PublishResult` la satisface por estructura, de modo que `classify` recibe
    el resultado real del publicador sin que el dominio tenga que importar la
    capa de infraestructura ni el resultado tenga que cambiar de forma.
    """

    error_code: ErrorCode | None
    failure_kind: FailureKind | None


# Fallos de los que el publicador puede recuperarse sin que nadie cambie nada:
# una red que vuelve, un timeout que no se repite, un 5xx que el servicio
# resuelve por su lado.
_TRANSIENT_FAILURE_KINDS = frozenset(
    {FailureKind.NETWORK, FailureKind.TIMEOUT, FailureKind.HTTP_5XX}
)


def classify(result: ClassifiableResult) -> ErrorCategory:
    """Clasifica un resultado del publicador en una de las tres categorías.

    Tabla de clasificación (US-C5.1 §4), en orden de evaluación:

    | Entrada                                        | Categoría    |
    |------------------------------------------------|--------------|
    | `QUOTA_EXCEEDED`                               | `DEFERRABLE` |
    | `OAUTH_ERROR`                                  | `PERMANENT`  |
    | `PUBLISH_FAILED` + red / timeout / HTTP 5xx     | `TRANSIENT`  |
    | todo lo demás                                  | `PERMANENT`  |

    Lo que cae en "todo lo demás", y por qué:

    - `OAUTH_ERROR`: ver ADR-0005. El refresh de tokens es US-C3 y todavía no
      está disponible, así que hoy no hay forma de recuperarse sin intervención
      humana.
    - HTTP 4xx de validación: reintentar la misma solicitud da el mismo 4xx.
    - `PUBLISH_FAILED` sin naturaleza conocida del fallo: el código es genérico
      (lo usa el publicador real para 5xx y timeouts, pero también el servicio
      para una excepción no controlada y el esqueleto de US-C3 para "no
      implementado"). Sin evidencia de que el fallo sea recuperable se asume lo
      conservador.
    - Cualquier código no reconocido, incluidos los del catálogo que no
      participan de la publicación: un catálogo que crezca no puede traducirse
      en reintentos potencialmente infinitos.
    - Un resultado sin código de error: un fallo mal formado no se reintenta.
      (Un resultado exitoso no se clasifica nunca; si igual se pasara, la
      respuesta conservadora es `PERMANENT`, nunca `TRANSIENT`.)
    """
    if result.error_code == ErrorCode.QUOTA_EXCEEDED:
        return ErrorCategory.DEFERRABLE

    if result.error_code == ErrorCode.OAUTH_ERROR:
        return ErrorCategory.PERMANENT

    if (
        result.error_code == ErrorCode.PUBLISH_FAILED
        and result.failure_kind in _TRANSIENT_FAILURE_KINDS
    ):
        return ErrorCategory.TRANSIENT

    return ErrorCategory.PERMANENT
