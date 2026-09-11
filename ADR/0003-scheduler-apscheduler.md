# ADR-0003 — APScheduler con job store en PostgreSQL

## Estado

Aceptada · 2026-09-10 · Sprint 1

## Contexto

US-C2 exige disparar publicaciones a su hora programada y, explícitamente,
"recuperarse correctamente tras un reinicio (job store persistente)". El
módulo corre en un contenedor que se reinicia en cada `docker compose up`,
que es exactamente como parte la demo del Sprint Review.

## Alternativas consideradas

1. **`cron` del sistema operativo.** Simple, pero vive fuera de la aplicación,
   no conoce el estado de la base de datos y obliga a un script separado con
   su propia configuración. Mala paridad dev/prod.
2. **Polling de la tabla `publication`.** Un bucle que cada N segundos busca
   filas vencidas. No requiere librería y es trivialmente recuperable tras un
   reinicio, pero la precisión depende del intervalo y hay que implementar a
   mano el bloqueo para evitar disparos duplicados.
3. **Celery beat + worker + Redis/RabbitMQ.** Potente y distribuible, pero
   agrega un broker adicional y dos procesos más al `docker compose` para un
   módulo que dispara pocos jobs.
4. **APScheduler con `SQLAlchemyJobStore` sobre PostgreSQL.** Corre en el
   mismo proceso de FastAPI, persiste los jobs en la base que ya tenemos y
   los recarga solo al arrancar.

## Decisión

Alternativa 4. `BackgroundScheduler` con `SQLAlchemyJobStore` apuntando a la
misma `DATABASE_URL` del módulo, arrancado desde el `lifespan` de FastAPI.

Configuración relevante:

- `misfire_grace_time = 3600`: si el contenedor estuvo caído a la hora del
  disparo, el job se ejecuta igual al volver, siempre que el atraso sea menor
  a una hora. Sin este margen APScheduler descarta el job silenciosamente.
- `coalesce = True`: varias ejecuciones atrasadas del mismo job se colapsan
  en una sola.
- `max_instances = 1`.

La idempotencia **no** se delega al scheduler. El handler toma la publicación
con un `UPDATE ... WHERE state = 'pending'` y solo procede si actualizó una
fila. Si dos ejecuciones concurren, una gana y la otra se retira sin publicar.

## Consecuencias

- Un solo proceso y una sola base de datos: el `docker compose` no crece.
- La recuperación tras reinicio es automática y verificada: se programó un
  job a +6 s, se mató el proceso, se esperó 8 s y al levantar de nuevo el job
  se recuperó y disparó.
- **Limitación conocida:** con job store compartido y varias réplicas del
  contenedor, el mismo job se dispara en todas. Corremos una única instancia.
  Si en el Sprint 4 se replica el servicio, hay que agregar un lock
  distribuido o migrar a la alternativa 3.
- APScheduler serializa la referencia a la función por su ruta de módulo
  (`app.services.publishing:execute_publication`). Si esa ruta cambia, los
  jobs ya guardados dejan de resolverse tras el reinicio. Renombrar ese módulo
  requiere una migración o vaciar `apscheduler_jobs`.
- Queda pendiente para US-C2 completa: cancelación de jobs con transición a
  `cancelled`, y la política de reintentos de US-C5.
