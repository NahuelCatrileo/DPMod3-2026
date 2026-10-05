# ADR-0004 — Recuperación tras reinicio: reconciliación y publicaciones vencidas

## Estado

Propuesta · 2026-10-04 · Sprint 2

Complementa a ADR-0003, que sigue vigente sin cambios.

## Contexto

US-C2 exige que el scheduler "se recupere correctamente tras un reinicio".
ADR-0003 resolvió el caso principal: los jobs viven en `apscheduler_jobs` y
APScheduler los recarga al arrancar. Al probarlo con el scheduler real
aparecieron tres casos que el job store solo no resuelve. En los tres la
publicación queda detenida para siempre y no se emite ningún evento, así que
el Módulo 4 nunca se entera:

1. **Caída más larga que `misfire_grace_time`.** APScheduler descarta el job
   en silencio y la fila queda en `pending`.
2. **Caída a mitad de la publicación.** La fila queda en `publishing` y ya no
   hay job que la retome.
3. **Job nunca registrado.** La fila se guardó pero el `add_job` no ocurrió
   (scheduler detenido, o caída entre el commit y el registro).

## Alternativas consideradas

1. **Dejarlo como está.** Cero código, pero el criterio de aceptación no se
   cumple en los tres casos y el fallo es silencioso.
2. **`misfire_grace_time = None` (sin límite).** Resuelve el caso 1
   publicando tarde, sin importar cuánto. Un video programado para el lunes
   podría salir el jueves sin que nadie lo decida. No resuelve 2 ni 3.
3. **Reconciliación con la tabla `publication` como fuente de verdad.** Al
   arrancar, y luego cada `SCHEDULER_RECONCILE_SECONDS` (60 s por defecto),
   se recorren las filas activas y se dejan los jobs coherentes con ellas.

## Decisión

Alternativa 3, implementada en `app/services/recovery.py`:

| Situación | Acción |
|---|---|
| `pending`, atraso mayor que el margen | `pending → failed`, emite `publish.failed` |
| `pending`, sin job en el store | se reprograma a su hora (o de inmediato si está atrasada dentro del margen) |
| `pending`, con job | no se toca |
| `publishing` al arrancar | `publishing → failed`, emite `publish.failed` |
| `publishing` en la ronda periódica | no se toca (puede ser un job en curso) |
| `published`, `failed`, `cancelled` | no se tocan |

- Las transiciones usan el mismo `UPDATE ... WHERE state = ...` del handler,
  así que si una ejecución real y la reconciliación compiten, solo una gana y
  se emite un único evento.
- El scheduler arranca en pausa, reconcilia y recién entonces dispara jobs.
- El job de reconciliación vive en un job store en memoria y no se cuenta en
  `pendingJobs` de `/api/health`.
- El evento es `publish.failed` con `errorCode = PUBLISH_FAILED`, ambos ya
  registrados en el catálogo. No se cambia ningún contrato.

**La decisión de producto que el equipo debe confirmar:** una publicación
vencida se marca fallida en lugar de publicarse tarde. Si el PO prefiere
publicar tarde, el cambio es poner `SCHEDULER_MISFIRE_GRACE_SECONDS` muy alto;
la reconciliación sigue cubriendo los casos 2 y 3.

## Consecuencias

- Los tres casos terminan en un estado final y con evento. Hay pruebas que lo
  demuestran, incluida una que mata un proceso real con `os._exit`.
- **Cambio en la máquina de estados de US-C6:** se agrega la transición
  `pending → failed`, que no estaba en la definición original. Si este ADR se
  rechaza, esa transición debe retirarse de `app/domain/states.py`.
- **`attempt: 0` en `publish.failed`:** una publicación vencida nunca se
  intentó, así que emite `attempt = 0`. El contrato no cambia, pero ese valor
  no se había usado; conviene confirmarlo con el Equipo D en la reunión de
  integración.
- Marcar `publishing → failed` al arrancar supone una sola instancia del
  servicio, la misma limitación que ya declara ADR-0003.
- La cancelación (`cancel_publication`) no tiene endpoint REST ni evento:
  ninguno de los dos está en el contrato y agregarlos requiere el
  procedimiento de la Guía del Estudiante §5.4.
