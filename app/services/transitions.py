"""US-C6 · Punto único de persistencia de un cambio de estado.

`app.domain.states.transition()` valida el par contra la matriz, pero la
validación en memoria no basta por sí sola: entre la lectura y la escritura
otro proceso (el job handler, la reconciliación o un reintento de US-C5) puede
haber movido la fila. Este módulo aplica las **dos** protecciones juntas, en
este orden, y es el único lugar donde se escribe un estado en la base:

1. **Dominio** — `transition()` comprueba el par (estado actual, estado nuevo)
   contra `VALID_TRANSITIONS` y lanza `InvalidTransitionError` si no está
   permitida. Lanza antes de emitir cualquier SQL, así que una transición
   inválida no toca la base.
2. **SQL** — el `UPDATE` va condicionado a que la fila siga en el estado de
   origen que se validó (`WHERE state = :origen`). Si otra ejecución ya la
   movió, `rowcount` es 0, no se escribe nada y el objeto se resincroniza con
   la base para no quedar con un estado parcial.

Antes de US-C6 cada servicio repetía su propio `UPDATE` condicional
(`publishing._claim`, `publishing._schedule_retry`, `recovery._fail_if_state`,
`recovery._requeue_if_state`): la guarda de concurrencia estaba bien, pero la
matriz no se consultaba en ninguna de esas rutas, así que no protegía nada.
Centralizarlo aquí hace que la guarda de concurrencia y la guarda de dominio
sean inseparables: no se puede tener una sin la otra.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domain.states import (
    InvalidTransitionError,
    PublishState,
    transition,
)
from app.infra.models import Publication

logger = logging.getLogger(__name__)


def transition_conditionally(
    session: Session,
    publication: Publication,
    new_state: PublishState,
    **values,
) -> bool:
    """Valida contra la matriz y persiste el cambio con UPDATE condicional.

    Devuelve True solo si esta ejecución ganó y la fila quedó en `new_state`.
    Devuelve False si la fila ya no estaba en el estado de origen (carrera
    perdida): el llamador decide si eso es un error o un caso normal.

    Lanza `InvalidTransitionError` si el par no está en la matriz, sin haber
    tocado la base.

    `**values` son las columnas adicionales que acompañan al cambio de estado
    (`attempts`, `last_error`, `youtube_video_id`, ...). Van en el mismo
    UPDATE para que estado y datos asociados se escriban juntos: no puede
    quedar una fila marcada como `published` sin su `youtube_video_id`.

    Orden deliberado: se valida **sin** marcar el objeto (`apply=False`),
    se escribe con el UPDATE condicional y solo entonces se sincroniza el
    objeto con `session.refresh()`. Marcar el objeto antes de escribir lo deja
    "sucio", y el ORM lo volcaría con un `UPDATE ... WHERE id` sin condición
    de estado en el commit o en el refresh del camino de carrera perdida:
    pisaría el estado bueno que dejó la otra ejecución. Es decir, se cumpliría
    la validación de dominio y la base quedaría igual mal. Ver
    `tests/test_state_machine.py::test_una_carrera_perdida_no_pisa_el_estado_bueno`.
    """
    origin = PublishState(publication.state)
    try:
        # Valida contra la matriz sin tocar el objeto.
        transition(publication, new_state, apply=False)
    except InvalidTransitionError:
        logger.warning(
            "transicion_rechazada publish_id=%s %s -> %s",
            publication.id,
            origin.value,
            new_state.value,
        )
        raise

    result = session.execute(
        update(Publication)
        .where(
            Publication.id == publication.id,
            Publication.state == origin.value,
        )
        .values(
            state=new_state.value,
            updated_at=datetime.now(dt_timezone.utc),
            **values,
        )
    )
    session.commit()

    # Una sola salida por rama: `refresh()` deja el objeto con el estado real
    # de la base en los dos casos, así que nunca sobrevive con un estado que
    # no esté persistido.
    session.refresh(publication)

    if result.rowcount != 1:
        logger.warning(
            "transicion_condicional_perdida publish_id=%s %s -> %s "
            "(el estado en la base ya no era el esperado)",
            publication.id,
            origin.value,
            new_state.value,
        )
        return False

    return True
