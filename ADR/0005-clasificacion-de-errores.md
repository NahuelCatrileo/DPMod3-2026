# ADR-0005 — Clasificación de errores del publicador para la política de reintentos

## Estado

Aceptada · 2026-10-07 · Sprint 3 · US-C5.1

> El apartado de `OAUTH_ERROR` es una decisión **del estado actual del sistema**,
> no de la arquitectura futura. Se revisa cuando US-C3 implemente el refresh de
> tokens.

## Contexto

US-C5 separa dos problemas: **qué clase de fallo es** cada error del publicador
(US-C5.1) y **qué se hace con él** (US-C5.3, ejecución de reintentos). Este ADR
cubre solo el primero; los reintentos, el backoff y el scheduler de reintentos
no están implementados.

Restricciones de partida:

- Por decisión de capas (ADR-0002), el publicador **no emite eventos**: devuelve
  un `PublishResult` y la capa de aplicación decide después. La clasificación es
  un dato de dominio, así que no puede depender de la infraestructura.
- Los códigos del contrato son tres: `QUOTA_EXCEEDED`, `OAUTH_ERROR` y
  `PUBLISH_FAILED` (Doc 1 §6.3). La lista registrada no se amplía sin el
  procedimiento de la Guía §5.4, que obliga a los otros tres equipos a mover su
  catálogo.
- `PUBLISH_FAILED` es **genérico**: el esqueleto de US-C3 lo usa para 5xx y
  timeouts, el servicio lo usa para una excepción no controlada y el propio
  esqueleto para "todavía no implementado". El mismo código cubre un fallo
  recuperable y uno que no lo es.
- El `errorCode` de `publish.failed` es contrato con los otros módulos: no puede
  cambiar de valores ni de forma (docs/contratos/eventos.md).

## Alternativas consideradas

1. **Un código registrado nuevo por cada causa** (`NETWORK_ERROR`, `TIMEOUT`,
   `HTTP_5XX`). Es lo más explícito, pero cambia el catálogo compartido y los
   valores posibles de `errorCode` en `publish.failed`: costo de integración
   alto por un dato que solo nos sirve a nosotros.
2. **Clasificar solo por código, con `PUBLISH_FAILED -> TRANSIENT`.** Una línea,
   pero mete en reintentos un 4xx de validación y una excepción no controlada,
   que es exactamente el reintento infinito que US-C5.1 pide evitar.
3. **Deducir la causa del texto de `reason`.** Cero cambios de estructura, pero
   la clasificación pasaría a depender de cómo se redactó un mensaje. Un test
   dejaría de ser determinista y traducir un texto rompería la política.
4. **Campo opcional `failure_kind` en `PublishResult` + `classify` puro en el
   dominio.** El publicador describe el fallo con un dato estructurado; la regla
   que lo traduce a categoría vive en el dominio, se prueba sin infraestructura
   y no toca el contrato de eventos. **Elegida.**
5. **Sobre `OAUTH_ERROR`: clasificarlo `TRANSIENT`** porque un token vencido es
   recuperable en teoría. Programaría reintentos que hoy no pueden funcionar:
   sin refresh, el mismo token vencido falla siempre.

## Decisión

Se implementa la alternativa 4. `app/domain/classification.py` define
`ErrorCategory`, `FailureKind`, la vista `ClassifiableResult` y la función pura
`classify(result) -> ErrorCategory`.

Tabla de clasificación:

| Entrada | Categoría | Motivo |
|---|---|---|
| `QUOTA_EXCEEDED` | `DEFERRABLE` | Hay que esperar a que la cuota se renueve |
| `PUBLISH_FAILED` + red, timeout o HTTP 5xx | `TRANSIENT` | El problema puede desaparecer sin cambiar nada |
| `OAUTH_ERROR` | `PERMANENT` | Ver abajo |
| `PUBLISH_FAILED` + HTTP 4xx de validación | `PERMANENT` | Reintentar la misma solicitud da el mismo 4xx |
| `PUBLISH_FAILED` sin naturaleza conocida | `PERMANENT` | Sin evidencia de recuperación, no se reintenta |
| Cualquier código no reconocido | `PERMANENT` | Evita reintentos potencialmente infinitos |

Tres decisiones que conviene poder defender sin leer el código:

- **`OAUTH_ERROR` se clasifica `PERMANENT`** porque el mecanismo de refresh de
  tokens pertenece a US-C3 y todavía no forma parte del alcance disponible.
  Reconocemos que un token vencido **podría** ser recuperable mediante refresh y,
  por tanto, potencialmente transitorio; la implementación actual lo clasifica
  como definitivo a propósito, para no programar reintentos que hoy no pueden
  tener éxito.
- **Un código desconocido es `PERMANENT`**, y esa regla manda incluso si el
  resultado trae una pista de transporte transitoria. Un catálogo que crezca no
  puede traducirse en reintentos sin límite; que el fallo se vea es preferible a
  que se reintente en silencio.
- **El dominio no importa `PublishResult`.** Declara la vista mínima que
  necesita (`ClassifiableResult`) y `PublishResult` la satisface por estructura.
  El dominio no depende de la infraestructura y el resultado de US-C4 no cambia
  de forma: `failure_kind` es un campo opcional al final, con default `None`, y
  **no viaja en `publish.failed`**.

## Consecuencias

- **Hasta que exista US-C3, un `OAUTH_ERROR` no genera ningún reintento
  automático.** El token vencido requiere re-autorización humana; el fallo queda
  en `failed` con su `errorCode` y es visible en `lastError` y en el evento.
- Cuando US-C3 implemente el refresh, este ADR se revisa: si el refresh pasa a
  recuperar el caso, `OAUTH_ERROR` se moverá a `TRANSIENT` con un cambio de una
  línea en `classify` más su fila en los tests parametrizados.
- US-C5.3 puede construir la política de reintentos consumiendo `ErrorCategory`
  sin volver a decidir la clasificación; US-C5.4 puede registrar la categoría
  junto al resultado.
- Riesgo asumido: si el publicador real de US-C3 devuelve `PUBLISH_FAILED` para
  un 5xx **sin** rellenar `failure_kind`, ese fallo se clasificará `PERMANENT` y
  no se reintentará. Es el lado conservador y está documentado en la tabla del
  esqueleto de `YouTubePublisher` para que se implemente junto con el mapeo.
- La cobertura de `classify` es del 100 % (ramas incluidas) y se verifica con
  `pytest --cov=app.domain.classification --cov-fail-under=100`.
- Fuera de alcance, y por lo tanto **no implementado**: ejecución de reintentos,
  backoff, política y scheduler de reintentos, refresh de OAuth, publicación de
  eventos nuevos, manejo real de cuotas e integración adicional con YouTube.
