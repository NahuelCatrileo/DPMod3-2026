# Contrato REST — Módulo 3 (Equipo C)

Versión 1 · Sprint 1 · Última actualización: 2026-09-10

El OpenAPI generado está en `http://localhost:8000/openapi.json` y la UI en
`http://localhost:8000/docs`. Exportarlo a `docs/contratos/openapi.json` en
cada PR que toque una firma.

## Endpoints

| Método y ruta | Request | Response | Historia |
|---|---|---|---|
| `POST /api/publish/schedule` | `{ contentId, scheduleAt, timezone }` | `202 { publishId, state, scheduleAt, timezone, correlationId }` | US-C1 |
| `GET /api/publish/{id}/status` | — | `200 { publishId, state, scheduleAt, timezone, attempts, youtubeVideoId?, lastError?, correlationId }` | US-C6 |
| `GET /api/health` | — | `200 { status, data }` | — |
| `POST /api/publish/{id}/now` | — | **No implementado** (US-C7, Could, Sprint 4) | US-C7 |

Cabecera opcional `X-Correlation-Id`: si viene, se propaga a la publicación y
a todos sus eventos. Si no, generamos uno.

## Sobre de error

```json
{ "status": "error", "code": "SCHEDULE_CONFLICT", "message": "..." }
```

## Mapeo de códigos

| Situación | HTTP | `code` |
|---|---|---|
| `scheduleAt` en el pasado | 409 | `SCHEDULE_CONFLICT` |
| El contenido ya tiene una publicación en `pending`/`publishing` | 409 | `SCHEDULE_CONFLICT` |
| Zona horaria inexistente | 422 | `INVALID_METADATA` |
| Campo faltante o mal formado | 422 | `INVALID_METADATA` |
| `publishId` inexistente | 404 | `CONTENT_NOT_FOUND` |

> **Decisión que conviene declarar en el Review.** La lista registrada no tiene
> un código para "dato de entrada inválido" en publicación. Usamos
> `INVALID_METADATA` para la zona horaria mal formada, reservando
> `SCHEDULE_CONFLICT` para su sentido literal (choque de programación).
> La alternativa era proponer un código nuevo vía Guía §5.4, pero eso obliga a
> los otros tres equipos a actualizar su catálogo por un caso menor.
> Si el docente o Equipo D prefieren otra cosa, es un cambio de una línea.

## Zona horaria

`scheduleAt` se acepta con o sin offset. Sin offset se interpreta en
`timezone`. Se **persiste siempre en UTC** y `timezone` se guarda aparte.
Motivo: `America/Santiago` cambia de offset dos veces al año; guardar hora
local haría que una publicación programada antes del cambio se dispare a la
hora equivocada.
