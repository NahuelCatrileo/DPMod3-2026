# Contrato de estados de publicación — Módulo 3 (Equipo C)

Versión 2 · Sprint 2 · Última actualización: 2026-09-24

Fuente de verdad en código: `app/domain/states.py` (`PublishState`,
`VALID_TRANSITIONS`). Este documento es el contrato para los Equipos B y D:
qué estados puede ver un consumidor de `publish.*` y en qué orden aparecen.

> Versión 2 (US-C6 + US-C5). La versión 1 describía la matriz mínima de US-C6.
> US-C5 agregó los reintentos automáticos (ADR-0006) y con ellos dos
> transiciones más; US-C6 las integró en el punto único de transición. La
> tabla de abajo es la matriz vigente.

## Estados

| Estado | Significado | ¿Estado final? |
|---|---|---|
| `pending` | Programada, esperando su hora o su próximo intento. Estado inicial de toda publicación. | No |
| `publishing` | El job la tomó y está subiendo el video. | No |
| `published` | Subida confirmada. Tiene `youtubeVideoId`. | Sí |
| `failed` | Fallo definitivo: error no reintentable o intentos agotados. Tiene `lastError`. | Sí (ver abajo) |
| `cancelled` | Reservado para la cancelación de US-C2 (cancelación de jobs, pendiente). | Sí |

> `cancelled` existe en el enum y en la matriz desde el Sprint 1, pero **ningún
> camino productivo lo asigna todavía**: la cancelación de jobs está pendiente
> en US-C2 y necesita además definir si emite un evento (no está en el catálogo
> de `docs/contratos/eventos.md`, así que requiere el procedimiento §5.4).
> Hasta entonces, Equipos B y D no deben esperar ver `cancelled`.

## Matriz de transiciones

La validez depende **siempre del par** (estado actual, estado nuevo), nunca de
que el estado destino sea conocido. Todo par que no esté marcado como "Sí" se
rechaza, incluidos los pares reflexivos (`pending → pending`, etc.).

| Estado actual | Estado siguiente | Permitido |
|---|---|:---:|
| `pending` | `pending` | No |
| `pending` | `publishing` | **Sí** |
| `pending` | `published` | No |
| `pending` | `failed` | **Sí** |
| `pending` | `cancelled` | **Sí** |
| `publishing` | `pending` | **Sí** |
| `publishing` | `publishing` | No |
| `publishing` | `published` | **Sí** |
| `publishing` | `failed` | **Sí** |
| `publishing` | `cancelled` | No |
| `published` | `pending` | No |
| `published` | `publishing` | No |
| `published` | `published` | No |
| `published` | `failed` | No |
| `published` | `cancelled` | No |
| `failed` | `pending` | **No** |
| `failed` | `publishing` | **Sí** |
| `failed` | `published` | No |
| `failed` | `failed` | No |
| `failed` | `cancelled` | No |
| `cancelled` | `pending` | No |
| `cancelled` | `publishing` | No |
| `cancelled` | `published` | No |
| `cancelled` | `failed` | No |
| `cancelled` | `cancelled` | No |

Total: **25 pares**, de los cuales **7 se aceptan** y **18 se rechazan**.

### Quién usa cada transición

| Transición | Quién la ejecuta | Nota |
|---|---|---|
| `pending → publishing` | Job handler (`_claim`) | Guarda de idempotencia: solo la primera ejecución gana. |
| `publishing → published` | Job handler | Éxito del publicador. Emite `publish.completed`. |
| `publishing → failed` | Job handler, reconciliación | Fallo definitivo o intentos agotados. Emite `publish.failed`. |
| `publishing → pending` | Job handler, reconciliación | **US-C5**: fallo transitorio o de cuota con intentos disponibles. Reprograma con backoff y **no** emite evento. |
| `pending → failed` | Reconciliación, job handler | **US-C5**: publicación vencida (ADR-0004) o fallo antes de tomarla. |
| `pending → cancelled` | — | Reservada a la cancelación de US-C2. Sin uso hoy. |
| `failed → publishing` | — | Permitida en la matriz desde el Sprint 1, pero **ningún camino productivo la usa hoy**: `_claim` solo actúa si la fila está en `pending`, así que un redisparo del job sobre una publicación fallida se retira sin publicar. Es el hueco que US-C7 (reintento manual) vendría a llenar. |

### Qué cambió en el Sprint 2

Tres transiciones se agregaron en este sprint, y US-C6 las unificó bajo una
sola validación:

| Transición | Origen | Por qué |
|---|---|---|
| `pending → failed` | US-C5 (ADR-0004) | Una publicación vencida que el scheduler no alcanzó a disparar dentro del margen tiene que poder registrarse como fallo, sin quedar colgada en `pending` para siempre. |
| `publishing → failed` | US-C5 (ADR-0006) | Ya se hacía en la práctica (el handler marcaba `failed` a mano); ahora está declarada en la matriz. |
| `publishing → pending` | US-C5 (ADR-0006) | Ante un fallo transitorio o de cuota con intentos disponibles, la publicación vuelve a la cola con un job de reintento. No es un estado nuevo: sigue siendo `pending`, así `GET /status` no cambia de contrato. |

No se agregó ningún estado nuevo. No se agregó `failed → pending`.

Equipos B y D: estas transiciones cambian el conjunto de estados que pueden
observar, no el catálogo de eventos. Un evento `publish.completed` sigue
implicando `published` y un `publish.failed` sigue implicando `failed`. Lo que
sí conviene tener presente es que **`publishing → pending` no emite ningún
evento**: una publicación puede volver a `pending` sin que se publique nada en
el bus, porque `publish.failed` está reservado al fallo definitivo.

## `failed` es terminal

```text
pending    → failed     ✅ permitido
publishing → failed     ✅ permitido
failed     → pending    ❌ NO permitido
```

**Decisión del Sprint 2: `failed` se considera terminal en la matriz actual.**
Una publicación fallida no vuelve a `pending` ni se reintenta sola.

Cuidado con la lectura apresurada de esta decisión: que `failed` sea terminal
**no** significa que toda publicación que falla se quede ahí. El reintento
automático de US-C5 ocurre **antes** de llegar a `failed`, como
`publishing → pending`. Una publicación solo llega a `failed` cuando ya no hay
nada que reintentar:

- el error es DEFINITIVO (token inválido, metadatos malos), o
- se agotaron los intentos (`PUBLISH_MAX_ATTEMPTS`).

Recién entonces se emite `publish.failed`, y desde ahí no hay salida.

- El reintento **manual** de una publicación fallida es **US-C7** (Could,
  Sprint 4) y todavía no está implementado. Cuando se implemente, será US-C7 el
  que introduzca la transición de vuelta (o el estado de reintento que se
  acuerde) y el que decida su política.
- US-C6 no adelanta esa decisión: agregarla ahora dejaría el reintento manual a
  medio camino.
- Existen pruebas explícitas de que `failed → pending` es inválida y de que la
  base queda sin cambios, y de que el reintento de US-C5 llega a `pending` sin
  pasar por `failed`: `tests/test_state_machine.py`.

## Un único punto de transición

La validación de la matriz vive en el dominio y la escritura en la capa de
servicios. Ninguna ruta productiva cambia un estado por su cuenta.

### Dominio — `app/domain/states.py`

`app.domain.states.transition(publication, new_state)` es el punto único de
transición:

1. lee el estado actual;
2. valida el par contra `VALID_TRANSITIONS`;
3. si el par no está, lanza `InvalidTransitionError` (error de dominio);
4. solo entonces escribe el estado en el objeto.

### Aplicación — `app/services/transitions.py`

`app.services.transitions.transition_conditionally(session, publication,
new_state, **values)` es el único lugar donde se **persiste** un cambio de
estado. Hace las dos protecciones juntas, en este orden:

1. **Dominio** — `transition(..., apply=False)` valida el par contra la matriz
   y lanza `InvalidTransitionError` si no está permitida, **antes de emitir
   cualquier SQL**.
2. **SQL** — el `UPDATE` va condicionado a que la fila siga en el estado de
   origen que se validó:

```sql
UPDATE publication
   SET state = :new_state, updated_at = :now, ...
 WHERE id = :id
   AND state = :estado_de_origen;   -- el que se validó contra la matriz
```

Si otra ejecución ya movió la fila, `rowcount` es 0, no se escribe nada y el
objeto se resincroniza con la base. Lo usan el job handler
(`services/publishing.py`) y la reconciliación (`services/recovery.py`): antes
cada uno repetía su propio `UPDATE` condicional y ninguno consultaba la matriz,
así que la guarda de concurrencia existía pero la de dominio no. Centralizarlo
hace que no se pueda tener una sin la otra.

> **Por qué `apply=False` y no marcar el objeto y escribir después.** El objeto
> viene de un ORM. Si se le cambia el estado antes de escribir, queda "sucio" y
> el ORM lo vuelca con un `UPDATE ... WHERE id` **sin condición de estado** en
> el commit o en el refresh del camino de carrera perdida: pisaría el estado
> bueno que dejó la otra ejecución. Se cumpliría la validación de dominio y la
> base quedaría igual de mal. Por eso se valida sin marcar, se escribe con el
> `UPDATE` condicional y recién después se refleja el resultado en el objeto
> con `session.refresh()`. Hay un test que lo cubre.

El estado inicial (`pending`) se establece al construir la publicación en
`services/scheduling.py`: eso es creación de la fila, no transición, así que no
pasa por la matriz.

### Qué error ve un consumidor

`InvalidTransitionError` hereda de `DomainError` y reutiliza el código
registrado `SCHEDULE_CONFLICT` (HTTP 409) en su sentido de "conflicto de
estado". No se inventó un código nuevo porque la lista de `ErrorCode` solo se
amplía por el procedimiento §5.4 (propuesta escrita, 3 días de aviso, acuerdo
en la reunión de integración). Hoy ninguna ruta REST puede provocar una
transición inválida: la matriz protege rutas internas.

## Verificación de las transiciones contra el publicador mock

Decisión registrada en el acta del Sprint 2 (`scrum/actas/sprint-2.md`):

> El criterio de que las transiciones reflejan el resultado real de la API se
> verifica durante el Sprint 2 utilizando el publicador mock. La verificación
> se repetirá contra la API real cuando se implemente US-C3.

Motivo: US-C3 (publicador real de YouTube) es Sprint 3. Mientras el publicador
sea `MockPublisher`, lo verificable es que la transición posterior a llamar al
publicador coincide con el resultado que el publicador devolvió: `published`
cuando devuelve éxito, y `pending` o `failed` según la clase del error y los
intentos disponibles. Eso es lo que cubren `tests/test_publishing_job.py`,
`tests/test_state_machine.py` y `tests/test_retry_policy.py`.

## Pruebas

`tests/test_state_machine.py` genera los 25 pares desde `VALID_TRANSITIONS` —
no desde una lista escrita a mano— y para cada par verifica:

- si está en la matriz: se acepta, el objeto cambia y **la fila se persiste**;
- si no está: se lanza `InvalidTransitionError` y **la fila queda intacta**.

Además cubre el `UPDATE` condicional con una carrera simulada entre dos
sesiones, el caso explícito `failed → pending` y la diferencia entre el
reintento de US-C5 (llega a `pending`) y el de US-C7 (todavía prohibido). Si la
matriz cambia, los tests cambian de expectativa solos y siguen siendo
exhaustivos.
