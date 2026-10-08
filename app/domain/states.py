"""Máquina de estados de la publicación.

Backlog US-C6: pending -> publishing -> published | failed
La usamos ya en el Sprint 1 porque el job handler de US-C2 necesita
la guarda de transición para ser idempotente (que un disparo doble no
publique dos veces). US-C5 agrega publishing -> pending para los
reintentos automáticos (ADR-0006).

US-C6 (Sprint 2) convierte esta validación en el **único** camino permitido
para cambiar el estado de una publicación: `transition()` es el punto único
de transición y toda ruta productiva (job handler, cancelación, futura
reconciliación y futuros reintentos) pasa por él.

Matriz completa, decisión sobre `failed` y verificación de las transiciones
contra el publicador mock: `docs/contratos/estados-publicacion.md`.
"""

from __future__ import annotations

import logging
from enum import Enum

from app.domain.errors import DomainError, ErrorCode

logger = logging.getLogger(__name__)


class PublishState(str, Enum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Matriz de transiciones válidas. Cualquier par que no esté aquí se rechaza,
# incluido el par (estado, el mismo estado): la validez depende siempre del
# par (estado actual, estado nuevo), nunca de que el destino sea un estado
# conocido.
VALID_TRANSITIONS: dict[PublishState, set[PublishState]] = {
    # pending -> failed: publicación vencida que el scheduler no alcanzó a
    # disparar dentro del margen (ADR-0004) o fallo detectado antes de tomar
    # la publicación (US-C6).
    PublishState.PENDING: {
        PublishState.PUBLISHING,
        PublishState.FAILED,
        PublishState.CANCELLED,
    },
    # publishing -> pending: fallo transitorio o de cuota mientras quedan
    # intentos; la publicación vuelve a la cola con su hora de reintento
    # (US-C5, ADR-0006). No es un estado nuevo: sigue siendo "pending",
    # así GET /status no cambia de contrato.
    PublishState.PUBLISHING: {
        PublishState.PUBLISHED,
        PublishState.FAILED,
        PublishState.PENDING,
    },
    # US-C7 (Could, Sprint 4): reintento manual failed -> pending
    PublishState.FAILED: {PublishState.PUBLISHING},
    PublishState.PUBLISHED: set(),
    PublishState.CANCELLED: set(),
}

# Estados en los que una publicación todavía "ocupa" el contenido.
ACTIVE_STATES = {PublishState.PENDING, PublishState.PUBLISHING}


def can_transition(origin: PublishState, target: PublishState) -> bool:
    return target in VALID_TRANSITIONS.get(origin, set())


def supports_transition(origin: PublishState, target: PublishState) -> bool:
    """Alias de intención de `can_transition`, para usarlo como precondición.

    Deja explícito en el código de servicio que se está consultando la matriz
    antes de adornar el fallo con su propio error de dominio, en vez de dejar
    escapar un `InvalidTransitionError` que el llamador no espera:

        if not supports_transition(PublishState(pub.state), DESTINO):
            raise ConflictoPropio(...)
    """
    return can_transition(origin, target)


class InvalidTransitionError(DomainError):
    """Transición rechazada por la matriz.

    Reutiliza la jerarquía de errores de dominio (`DomainError`), así que el
    sobre REST estándar la traduce sin código extra. No inventa un código
    nuevo: la lista de `ErrorCode` solo cambia por el procedimiento §5.4, así
    que reutilizamos SCHEDULE_CONFLICT en su sentido de "conflicto de estado".
    El mapeo queda declarado en `docs/contratos/rest.md`.
    """

    def __init__(self, origin: PublishState, target: PublishState) -> None:
        self.origin = origin
        self.target = target
        super().__init__(
            ErrorCode.SCHEDULE_CONFLICT,
            f"Transición inválida: {origin.value} -> {target.value}",
            http_status=409,
        )


def assert_transition(origin: PublishState, target: PublishState) -> None:
    """Valida el par contra la matriz sin escribir nada.

    Es la pieza que comparten `transition()` (aplica en memoria) y la capa de
    persistencia (valida, escribe con UPDATE condicional y recién después
    sincroniza el objeto).
    """
    if not can_transition(origin, target):
        raise InvalidTransitionError(origin, target)


def transition(
    publication, new_state: PublishState, *, apply: bool = True
) -> PublishState:
    """Punto ÚNICO de transición de estados (US-C6).

    Pasos, en este orden a propósito:

    1. lee el estado actual del objeto;
    2. valida el par (estado actual, estado nuevo) contra la matriz;
    3. lanza `InvalidTransitionError` si el par no está en la matriz;
    4. solo entonces escribe el estado en el objeto (salvo `apply=False`).

    Como el estado se escribe después de validar, una transición inválida deja
    el objeto intacto: no hay estados parcialmente modificados.

    `apply=False` existe por una razón concreta, no por comodidad. Cuando
    detrás del objeto hay un ORM, marcar el objeto deja la fila "sucia" y el
    ORM puede volcarla con un `UPDATE ... WHERE id` **sin condición de
    estado** en el siguiente flush. Si después el UPDATE condicional pierde la
    carrera, ese volcado ya escribió el estado equivocado en la base: se
    cumplió la validación de dominio y aun así la base quedó mal. Por eso la
    capa de persistencia (`app.services.transitions.transition_conditionally`)
    valida con `apply=False`, escribe con el UPDATE condicional y solo después
    refleja el resultado en el objeto con `session.refresh()`.
    """
    origin = PublishState(publication.state)
    assert_transition(origin, new_state)
    if apply:
        publication.state = new_state.value
    logger.debug(
        "transicion_estado publish_id=%s %s -> %s (apply=%s)",
        getattr(publication, "id", None),
        origin.value,
        new_state.value,
        apply,
    )
    return new_state
