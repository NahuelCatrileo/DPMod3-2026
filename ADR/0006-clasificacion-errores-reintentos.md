# ADR-0006 — Clasificación de errores y política de reintentos (US-C5)

## Estado

Propuesta · 2026-10-07 · Sprint 2

Complementa a ADR-0004 (reconciliación) y ADR-0003 (scheduler); ambas siguen
vigentes.

## Contexto

US-C5 pide que, ante un error de publicación, el sistema distinga entre
errores **transitorios** (se reintenta con espera creciente), **diferibles**
(se esperan minutos, p. ej. cuota agotada) y **definitivos** (no tiene sentido
reintentar), y que, superado el límite de reintentos, emita `publish.failed`
con la causa.

El catálogo del Doc 1 §6.3 ya tiene los códigos (`PUBLISH_FAILED`,
`QUOTA_EXCEEDED`, `OAUTH_ERROR`, …), pero hasta ahora **todo** fallo iba
directo a `failed` al primer intento. No existía la clase del error ni el
concepto de "aún no es un fallo definitivo".

## Alternativas consideradas

1. **Reintentar todo.** Simple, pero reintenta `SCHEDULE_CONFLICT` y
   `INVALID_METADATA`, que no se solucionan solos: solo retrasa el fallo y
   gasta cuota.
2. **No reintentar nada (estado actual).** Un corte de red de un segundo ya
   deja la publicación en `failed` y obliga a un reintento manual (US-C7),
   que todavía no existe.
3. **Clasificar cada código del catálogo y acotar los reintentos a las
   clases que pueden resolverse solas**, con un máximo de intentos global
   (mismo límite para todas las clases) y dos delays: backoff exponencial
   para transitorios y un delay fijo largo para cuota.

## Decisión

Alternativa 3. Implementada en tres puntos:

| Pieza | Archivo | Qué hace |
|---|---|---|
| Clasificación | `app/domain/errors.py` | `ErrorClass` + tabla `ERROR_CLASS_BY_CODE` + `classify()`. Códigos sin entrada → `DEFINITIVO`. |
| Política | `app/services/retry.py` | `should_retry(clase, intentos)` y `retry_delay_seconds(clase, intentos)`. |
| Aplicación | `app/services/publishing.py` · `app/services/recovery.py` | El handler decide entre reprogramar y fallar; la reconciliación reprograma los reintentos y trata la interrupción como transitorio. |

Mapeo aprobado:

| Clase | Códigos | Delay |
|---|---|---|
| `transitorio` | `PUBLISH_FAILED`, `EVENT_VALIDATION_ERROR` | Exponencial: base · 2^(n−1) → 60 s, 120 s, 240 s… |
| `diferible` | `QUOTA_EXCEEDED` | Fijo: `PUBLISH_QUOTA_RETRY_SECONDS` (3600 s) |
| `definitivo` | `OAUTH_ERROR`, `UNAUTHORIZED`, `INVALID_METADATA`, `CONTENT_NOT_FOUND`, `DUPLICATE_CONTENT`, `SCHEDULE_CONFLICT` | Sin reintento |

Reglas de la política:

- **Máximo `PUBLISH_MAX_ATTEMPTS` (3) intentos** por publicación, contados en
  la columna `attempts` que ya existía (Doc 3 §6): no hay migración.
- **Reintento = `publishing → pending`** con `UPDATE` condicional (mismo
  esquema que `_claim`) y un job nuevo con la hora del próximo intento.
  Es la única transición nueva de la máquina de estados de US-C6.
- **`publish.failed` solo en el fallo definitivo o con los intentos
  agotados.** Durante los reintentos no se emite ningún evento: solo un log
  con `correlationId`. Coherente con "fallida definitiva" del catálogo y con
  ADR-0004, que ya emite `attempt: 0` cuando nunca se intentó.
- El detalle del último fallo queda en `last_error` con formato
  `CODIGO: motivo`, que `GET /status` ya exponía. **Los esquemas no cambian**
  (Guía §5.4): `errorCode` de `publish.failed` y `attempts`/`lastError` del
  status son los mismos campos de siempre.
- **La clase no se expone como campo nuevo** en eventos ni en el status: es
  una decisión interna. Si en la reunión de integración piden verla, se
  agrega como atributo derivado bajo §5.4.

Reconciliación (cambios sobre ADR-0004):

- "Vencida" solo aplica a `attempts == 0`. Una fila con intentos no está
  perdida: espera su reintento, cuya hora se reconstruye con
  `updated_at + delay` (la clase sale de `last_error`), de modo que el
  backoff sobrevive a la caída del proceso.
- Una publicación **interrumpida** (`publishing` huérfano al arrancar) es un
  fallo transitorio: si quedan intentos vuelve a `pending`; si no, `failed`
  como en ADR-0004.

## Consecuencias

- Un corte transitorio ya no deja la publicación en `failed`: se reintenta
  sola hasta agotar el límite y recién ahí cae `publish.failed`.
- **`publish.failed` puede llegar tarde** respecto al fallo real (tras ~7 min
  con la configuración por defecto). Si el Módulo 4 asume que el fallo es
  inmediato, conviene avisarlo en la reunión de integración.
- **La demo del camino de error cambia:** con `MOCK_FAILURE_CODE=QUOTA_EXCEEDED`
  (default) el mock falla en diferible y hay que esperar los reintentos; para
  mostrar `failed` al primer intento se usa `MOCK_FAILURE_CODE=OAUTH_ERROR`.
- `GET /status` puede mostrar `state: pending` con `attempts > 0` y
  `lastError` distinto de null: es el estado "esperando reintento". El esquema
  no cambia, pero es nuevo comportamiento que conviene declarar.
- El reintento manual (US-C7) sigue sin existir: la política de este ADR es
  automática. Si US-C7 llega, debe respetar `PUBLISH_MAX_ATTEMPTS` o
  reiniciar el contador, decisión de ese sprint.
- `publishing → pending` debe aceptarse en la matriz de transiciones de
  US-C6; si este ADR se rechaza, hay que retirarla de `app/domain/states.py`.
