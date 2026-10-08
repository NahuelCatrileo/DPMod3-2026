"""US-C6.2 — Pruebas exhaustivas de la máquina de estados de publicación.

Qué garantiza esta suite:

1. **Todos los pares** estado-origen × estado-destino (incluidos los pares
   reflexivos `x -> x`, que la matriz rechaza) se prueban uno por uno.
2. Los pares que están en la matriz se aceptan y se **persisten**.
3. Los pares que no están lanzan `InvalidTransitionError` (error de dominio)
   y dejan la fila en la base **sin cambios**.
4. `failed` es terminal en esta versión: `failed -> pending` es inválida de
   forma explícita, no implícita.
5. El UPDATE condicional sigue vivo: una transición que no encuentra el
   estado de origen esperado en la base no escribe nada.

La regla de aceptación sale de `VALID_TRANSITIONS` (la matriz), no de una
lista escrita a mano en el test: si mañana la matriz cambia, estos tests
cambian de expectativa solos y siguen siendo exhaustivos.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.errors import DomainError, ErrorCode
from app.domain.states import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    PublishState,
    can_transition,
    transition,
)
from app.infra.models import Publication
from app.infra.publishers.factory import set_publisher
from app.infra.publishers.mock import MockPublisher
from app.services.publishing import _transition_conditionally, execute_publication

TODOS_LOS_ESTADOS = list(PublishState)

# (origen, destino) presentes en la matriz.
PARES_VALIDOS = [
    (origen, destino)
    for origen in TODOS_LOS_ESTADOS
    for destino in TODOS_LOS_ESTADOS
    if can_transition(origen, destino)
]

# (origen, destino) ausentes en la matriz. En el sistema real son la mayoría,
# incluidos todos los pares reflexivos.
PARES_INVALIDOS = [
    (origen, destino)
    for origen in TODOS_LOS_ESTADOS
    for destino in TODOS_LOS_ESTADOS
    if not can_transition(origen, destino)
]

TODOS_LOS_PARES = [
    (origen, destino)
    for origen in TODOS_LOS_ESTADOS
    for destino in TODOS_LOS_ESTADOS
]


def _crear_publicacion(session, content_id: str = "c-c6") -> Publication:
    """Crea una fila real en la base, en estado pending.

    `pending` es el estado inicial de una publicación: viene del constructor
    (`state=PublishState.PENDING.value` en `schedule_publication`), no de una
    transición, así que no pasa por la matriz.
    """
    publicacion = Publication(
        content_id=content_id,
        state=PublishState.PENDING.value,
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        timezone="America/Santiago",
        attempts=0,
    )
    session.add(publicacion)
    session.commit()
    session.refresh(publicacion)
    return publicacion


def _forzar_estado(session, publicacion: Publication, estado: PublishState) -> None:
    """Prepara el escenario: coloca la fila en un estado concreto.

    Es montaje de escenario, no una transición de negocio, así que escribe
    directo en la base a propósito y por eso vive en el test. Sirve para poder
    probar los pares cuyo origen no es `pending`. La validez del par la decide
    la matriz dentro de `transition()`, no este ayudante.
    """
    publicacion.state = estado.value
    session.commit()
    session.refresh(publicacion)


def _en_estado(estado: PublishState) -> Publication:
    """Publicación en memoria, sin persistir. Sirve para probar solo dominio."""
    return Publication(
        content_id="c-domain",
        state=estado.value,
        schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        timezone="UTC",
    )


def _estado_persistido(session, publish_id: str) -> str:
    """Lee el estado real desde la fila, no desde el objeto en memoria.

    Después de un UPDATE directo el objeto ORM puede quedar con el valor que
    el dominio validó aunque la base no lo haya aceptado: eso es justo lo que
    estas pruebas deben detectar. Por eso se lee la columna con un SELECT
    nuevo y no `publication.state`.
    """
    estado = session.execute(
        select(Publication.state).where(Publication.id == publish_id)
    ).scalar_one()
    assert isinstance(estado, str)
    return estado


# --- 1. Matriz completa: todos los pares, válidos e inválidos ---------------


def test_la_matriz_cubre_todos_los_estados_del_enum():
    """Si alguien agrega un estado al enum y no lo pone en la matriz, falla."""
    assert set(VALID_TRANSITIONS) == set(PublishState)


@pytest.mark.parametrize("origen,destino", TODOS_LOS_PARES)
def test_matriz_cubre_todos_los_pares(origen, destino):
    """Cada par del producto cartesiano tiene una respuesta definida.

    `can_transition` nunca revienta y siempre devuelve un booleano: la matriz
    decide, no un `KeyError`.
    """
    assert isinstance(can_transition(origen, destino), bool)


# --- 2. `transition()` en el dominio: acepta o rechaza, sin base ------------


@pytest.mark.parametrize("origen,destino", PARES_VALIDOS)
def test_transicion_valida_actualiza_el_objeto(origen, destino):
    publicacion = _en_estado(origen)

    resultado = transition(publicacion, destino)

    assert resultado is destino
    assert publicacion.state == destino.value


@pytest.mark.parametrize("origen,destino", PARES_INVALIDOS)
def test_transicion_invalida_lanza_error_de_dominio_y_no_toca_el_objeto(
    origen, destino
):
    publicacion = _en_estado(origen)

    with pytest.raises(InvalidTransitionError) as excinfo:
        transition(publicacion, destino)

    # Es un error de dominio (sobre REST estándar), no una excepción suelta.
    assert isinstance(excinfo.value, DomainError)
    assert excinfo.value.code == ErrorCode.SCHEDULE_CONFLICT
    assert (excinfo.value.origin, excinfo.value.target) == (origen, destino)
    # Sin modificaciones parciales: el objeto queda como estaba.
    assert publicacion.state == origen.value


# --- 3. Persistencia: las válidas escriben, las inválidas no ----------------


@pytest.mark.parametrize("origen,destino", PARES_VALIDOS)
def test_transicion_valida_se_persiste(session, origen, destino):
    publicacion = _crear_publicacion(session)
    _forzar_estado(session, publicacion, origen)

    assert _transition_conditionally(session, publicacion, destino) is True

    assert publicacion.state == destino.value
    assert _estado_persistido(session, publicacion.id) == destino.value


@pytest.mark.parametrize("origen,destino", PARES_INVALIDOS)
def test_transicion_invalida_no_modifica_la_base(session, origen, destino):
    publicacion = _crear_publicacion(session)
    _forzar_estado(session, publicacion, origen)

    with pytest.raises(InvalidTransitionError):
        _transition_conditionally(session, publicacion, destino)

    # El objeto y, sobre todo, la fila en la base siguen en el estado original.
    assert publicacion.state == origen.value
    assert _estado_persistido(session, publicacion.id) == origen.value


def test_una_transicion_invalida_no_emite_ningun_evento(session, events):
    """Si la transición se rechaza, no puede salir un evento que sugiera que
    la publicación avanzó."""
    publicacion = _crear_publicacion(session)
    _forzar_estado(session, publicacion, PublishState.PUBLISHED)

    with pytest.raises(InvalidTransitionError):
        _transition_conditionally(session, publicacion, PublishState.FAILED)

    assert events.published == []


def test_una_carrera_perdida_no_pisa_el_estado_bueno(session):
    """Regresión: la validación de dominio no puede dejar el objeto "sucio".

    Si el punto único marcara el objeto antes de escribir, el ORM lo volcaría
    con un `UPDATE ... WHERE id` **sin condición de estado** en el commit o en
    el refresh del camino de carrera perdida, pisando el estado que dejó la
    ejecución ganadora: se cumpliría la validación de dominio y la base
    quedaría igual de mal.

    El test arma esa secuencia sin hilos: la fila se mueve a `failed` y el
    objeto de **esta** sesión queda con la foto vieja y sucio, como si hubiera
    leído antes de que el otro escribiera. Tiene que ser esta sesión, porque es
    la que flusharía el objeto.
    """
    publicacion = _crear_publicacion(session)
    _forzar_estado(session, publicacion, PublishState.PUBLISHING)

    # El ganador deja la fila en `failed`.
    assert _transition_conditionally(session, publicacion, PublishState.FAILED)

    # El perdedor trae la foto vieja: se expira el atributo (sin tocar la base,
    # a diferencia de un refresh) y se ensucia el objeto a mano. Queda
    # desfasado (`publishing`) Y sucio, que es la combinación peligrosa.
    session.expire(publicacion, ["state"])
    publicacion.state = PublishState.PUBLISHING.value
    assert publicacion.state == PublishState.PUBLISHING.value
    assert _estado_persistido(session, publicacion.id) == PublishState.FAILED.value

    # `publishing -> published` está en la matriz, pero la fila ya no está en
    # `publishing`: el UPDATE condicional no encuentra nada.
    assert (
        _transition_conditionally(session, publicacion, PublishState.PUBLISHED)
        is False
    )

    # Lo que manda es el estado del ganador, no lo que el perdedor tenía en
    # memoria ni lo que el dominio había validado.
    assert _estado_persistido(session, publicacion.id) == PublishState.FAILED.value
    assert publicacion.state == PublishState.FAILED.value


# --- 4. `failed` es terminal en esta versión --------------------------------


def test_failed_a_pending_es_invalida():
    """Decisión explícita de US-C6: el reintento manual (US-C7) es el que
    introducirá esta transición, y todavía no existe."""
    assert can_transition(PublishState.FAILED, PublishState.PENDING) is False
    with pytest.raises(InvalidTransitionError):
        transition(_en_estado(PublishState.FAILED), PublishState.PENDING)


def test_failed_a_pending_no_modifica_la_base(session):
    publicacion = _crear_publicacion(session)
    _forzar_estado(session, publicacion, PublishState.FAILED)

    with pytest.raises(InvalidTransitionError):
        _transition_conditionally(session, publicacion, PublishState.PENDING)

    assert _estado_persistido(session, publicacion.id) == PublishState.FAILED.value


def test_us_c7_no_esta_implementada():
    """US-C6 no adelanta US-C7: ni el estado destino del reintento ni la
    transición de vuelta están en la matriz."""
    assert can_transition(PublishState.FAILED, PublishState.PENDING) is False


@pytest.mark.parametrize(
    "origen,destino",
    [
        (PublishState.PENDING, PublishState.PUBLISHING),
        (PublishState.PENDING, PublishState.FAILED),
        (PublishState.PUBLISHING, PublishState.FAILED),
        (PublishState.PUBLISHING, PublishState.PENDING),
        (PublishState.PUBLISHING, PublishState.PUBLISHED),
    ],
)
def test_transiciones_de_la_matriz(origen, destino):
    """Las tres transiciones nuevas de US-C6 (pending -> failed,
    publishing -> failed, publishing -> pending) están en la matriz, igual
    que las que ya existían desde el Sprint 1."""
    assert can_transition(origen, destino) is True


# --- 5. UPDATE condicional: la protección SQL sigue activa ------------------


def test_update_condicional_no_escribe_si_el_estado_de_origen_cambio(
    session, events
):
    """Carrera entre dos ejecuciones, sin hilos: la segunda llega con el
    objeto leído antes de que la primera escribiera.

    La validación de dominio pasa (publishing -> published está permitida),
    pero el UPDATE condicional no encuentra la fila en `publishing` porque
    otra ejecución ya la movió a `failed`. No se escribe nada: es exactamente
    la protección que la historia pide conservar.
    """
    publicacion = _crear_publicacion(session)
    _forzar_estado(session, publicacion, PublishState.PUBLISHING)

    OtraSesion = sessionmaker(bind=session.get_bind())
    desfasada = OtraSesion()
    try:
        copia = desfasada.get(Publication, publicacion.id)
        assert copia is not None and copia.state == PublishState.PUBLISHING.value

        # Otra ejecución gana la carrera y registra el fallo.
        assert _transition_conditionally(session, publicacion, PublishState.FAILED)

        # Esta ejecución venía con la foto vieja (publishing) y llega tarde:
        # quiere marcar published sobre una fila que ya es failed.
        assert (
            _transition_conditionally(desfasada, copia, PublishState.PUBLISHED)
            is False
        )

        # No se pisó el estado bueno y el objeto desfasado se resincronizó.
        assert _estado_persistido(session, publicacion.id) == (
            PublishState.FAILED.value
        )
        assert copia.state == PublishState.FAILED.value
        assert copia.youtube_video_id is None
    finally:
        desfasada.close()


def test_el_job_usa_el_punto_unico_de_transicion(session, events, publisher):
    """Camino productivo completo: el job handler mueve pending -> publishing
    -> published y la base termina en el estado esperado."""
    publicacion = _crear_publicacion(session, content_id="c-job")

    execute_publication(publicacion.id)

    assert _estado_persistido(session, publicacion.id) == (
        PublishState.PUBLISHED.value
    )


def test_el_job_no_republica_una_publicacion_ya_publicada(session, publisher):
    """published no admite ninguna salida: un segundo disparo del mismo job se
    retira sin publicar de nuevo y sin reventar."""
    publicacion = _crear_publicacion(session, content_id="c-job-2")
    execute_publication(publicacion.id)

    execute_publication(publicacion.id)

    assert _estado_persistido(session, publicacion.id) == (
        PublishState.PUBLISHED.value
    )


def test_un_job_sobre_una_publicacion_fallida_no_la_devuelve_a_pending(
    session, events
):
    """`failed` es terminal: ningún disparo posterior la revive.

    Se usa un error DEFINITIVO (OAUTH_ERROR) a propósito. Con la política de
    US-C5, un error transitorio o de cuota no llega a `failed`: el handler
    devuelve la publicación a `pending` con un job de reintento
    (`_schedule_retry`), que es una transición `publishing -> pending` y no
    una salida de `failed`. Este test aísla el caso terminal: `failed` no
    tiene vuelta a `pending` en la matriz, así que el segundo disparo se
    retira en `_claim` sin publicar y sin tocar la fila.
    """
    set_publisher(
        MockPublisher(
            latency_seconds=0,
            failure_rate=1.0,
            error_code=ErrorCode.OAUTH_ERROR,
        )
    )
    try:
        publicacion = _crear_publicacion(session, content_id="c-job-3")
        execute_publication(publicacion.id)
        assert _estado_persistido(session, publicacion.id) == (
            PublishState.FAILED.value
        )

        eventos_antes = len(events.events_of_type("publish.failed"))

        # Segundo disparo del mismo job: `failed -> publishing` está en la
        # matriz, pero el handler no lo intenta porque la publicación ya no
        # está en `pending`. La fila queda como estaba.
        execute_publication(publicacion.id)
        assert _estado_persistido(session, publicacion.id) == (
            PublishState.FAILED.value
        )
        assert len(events.events_of_type("publish.failed")) == eventos_antes
    finally:
        set_publisher(None)

    # Ningún camino productivo movió la fila a `pending`.
    assert _estado_persistido(session, publicacion.id) != PublishState.PENDING.value


def test_el_reintento_de_us_c5_vuelve_a_pending_sin_pasar_por_failed(session, events):
    """US-C5 dentro del marco de US-C6: un error DIFERIBLE con intentos
    disponibles es una transición `publishing -> pending`, permitida por la
    matriz, y no una salida de `failed`.

    Documenta la diferencia entre "reintentar la publicación" (US-C5, vuelve a
    `pending`) y "reintento manual de una publicación fallida" (US-C7, sería
    `failed -> pending`, todavía no permitido).
    """
    set_publisher(
        MockPublisher(
            latency_seconds=0,
            failure_rate=1.0,
            error_code=ErrorCode.QUOTA_EXCEEDED,
        )
    )
    try:
        publicacion = _crear_publicacion(session, content_id="c-job-4")
        execute_publication(publicacion.id)

        # Quedó esperando el reintento, no fallada: publish.failed está
        # reservado al fallo definitivo.
        assert _estado_persistido(session, publicacion.id) == (
            PublishState.PENDING.value
        )
        assert events.events_of_type("publish.failed") == []
    finally:
        set_publisher(None)


# --- 6. Guarda contra un falso verde ----------------------------------------


def test_estado_persistido_lee_la_columna_y_no_el_objeto(session):
    """Si `_estado_persistido` leyera `publication.state`, todas las
    aserciones de "la base no cambió" serían falsas: comprobarían el objeto en
    memoria, no la base. Aquí se verifica que lee la fila."""
    publicacion = _crear_publicacion(session)
    publicacion.state = PublishState.PUBLISHED.value  # solo en memoria

    assert _estado_persistido(session, publicacion.id) == PublishState.PENDING.value
