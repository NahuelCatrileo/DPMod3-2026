"""Máquina de estados de la publicación.

Backlog US-C6: pending -> publishing -> published | failed
La usamos ya en el Sprint 1 porque el job handler de US-C2 necesita
la guarda de transición para ser idempotente (que un disparo doble no
publique dos veces).
"""

from enum import Enum


class PublishState(str, Enum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Matriz de transiciones válidas. Cualquier par que no esté aquí se rechaza.
VALID_TRANSITIONS: dict[PublishState, set[PublishState]] = {
    PublishState.PENDING: {PublishState.PUBLISHING, PublishState.CANCELLED},
    PublishState.PUBLISHING: {PublishState.PUBLISHED, PublishState.FAILED},
    # US-C7 (Could, Sprint 4): reintento manual failed -> publishing
    PublishState.FAILED: {PublishState.PUBLISHING},
    PublishState.PUBLISHED: set(),
    PublishState.CANCELLED: set(),
}

# Estados en los que una publicación todavía "ocupa" el contenido.
ACTIVE_STATES = {PublishState.PENDING, PublishState.PUBLISHING}


def can_transition(origin: PublishState, target: PublishState) -> bool:
    return target in VALID_TRANSITIONS.get(origin, set())


class InvalidTransitionError(Exception):
    def __init__(self, origin: PublishState, target: PublishState) -> None:
        self.origin = origin
        self.target = target
        super().__init__(f"Transición inválida: {origin.value} -> {target.value}")


def assert_transition(origin: PublishState, target: PublishState) -> None:
    if not can_transition(origin, target):
        raise InvalidTransitionError(origin, target)
