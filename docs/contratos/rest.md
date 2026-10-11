# Contrato REST — Módulo 3 (Equipo C)

Versión 1 · Sprint 1 · Última actualización: 2026-10-10

El OpenAPI generado está en `http://localhost:8000/openapi.json` y la UI en
`http://localhost:8000/docs`. Exportarlo a `docs/contratos/openapi.json` en
cada PR que toque una firma.

## Endpoints

| Método y ruta | Request | Response | Historia |
|---|---|---|---|
| `POST /api/publish/schedule` | `{ contentId, scheduleAt, timezone }` | `202 { publishId, state, scheduleAt, timezone, correlationId }` | US-C1 |
| `GET /api/publish/{id}/status` | — | `200 { publishId, state, scheduleAt, timezone, attempts, youtubeVideoId?, lastError?, correlationId }` | US-C6 |
| `GET /api/health` | — | `200 { status, data }` | — |
| `POST /api/auth/token` | formulario `grant_type=client_credentials`, `client_id`, `client_secret` (o HTTP Basic) | `200 { access_token, token_type, expires_in }` | ADR-0007 |
| `POST /api/publish/{id}/now` | — | **No implementado** (US-C7, Could, Sprint 4) | US-C7 |

Todas las rutas `/api/publish/*` exigen la cabecera
`Authorization: Bearer <jwt>` (ADR-0005). `GET /api/health` y
`POST /api/auth/token` quedan públicos.

El token lo emite este módulo: `POST /api/auth/token` lo entrega a los
clientes de servicio registrados en `AUTH_CLIENTS` (ADR-0007). El API Gateway
(Equipo D) lo valida con la misma clave (`JWT_SECRET` allá, `JWT_SECRET_KEY`
aquí) y este módulo lo verifica de nuevo. Sigue el flujo *client credentials*
de OAuth 2.0 (RFC 6749 §4.4), por eso el pedido va como formulario y la
respuesta usa `access_token` / `expires_in` en vez del sobre
`{ status, data }`. Los errores sí usan el sobre acordado.

Claims del token: `sub` (el `client_id`), `role`, `iat`, `exp`, `jti` e `iss`
si se configura `JWT_ISSUER`. No lleva `user_id`: el gateway usa `sub` cuando
falta. Tampoco `aud`, porque el gateway rechaza los tokens que la traen.
El gateway todavía no enruta `POST /api/auth/token`, así que el token se pide
directo al módulo.

Cabecera opcional `X-Correlation-Id`: si viene, se propaga a la publicación y
a todos sus eventos. Si no, generamos uno.

## Sobre de error

```json
{ "status": "error", "code": "SCHEDULE_CONFLICT", "message": "..." }
```

## Semántica de `GET /status` con reintentos (US-C5)

El esquema no cambia; sí cambia qué combinación de valores puede aparecer:

| Valor | Significado |
|---|---|
| `state: pending` con `attempts: 0` | Esperando su hora de publicación. |
| `state: pending` con `attempts > 0` | **Esperando un reintento.** El último intento falló; `lastError` trae `CODIGO: motivo`. El próximo intento se calcula como `updated_at` + backoff (el campo `updatedAt` no está en la respuesta, así que el momento exacto solo está en los logs con `correlationId`). |
| `state: publishing` con `attempts > 0` | Reintento en curso. |
| `state: failed` | Fallo definitivo o intentos agotados. `lastError` conserva el último error. |
| `lastError` | Formato `CODIGO: motivo`, con los códigos de la lista registrada. |

Reintentos: máximo `PUBLISH_MAX_ATTEMPTS` (3) intentos por publicación;
backoff exponencial de `PUBLISH_RETRY_BACKOFF_SECONDS` (60 s, 120 s, 240 s…)
para errores transitorios y `PUBLISH_QUOTA_RETRY_SECONDS` (3600 s) para
`QUOTA_EXCEEDED`. Detalle del mapeo en
`ADR/0006-clasificacion-errores-reintentos.md`.

## Mapeo de códigos

| Situación | HTTP | `code` |
|---|---|---|
| `scheduleAt` en el pasado | 409 | `SCHEDULE_CONFLICT` |
| El contenido ya tiene una publicación en `pending`/`publishing` | 409 | `SCHEDULE_CONFLICT` |
| Transición de estado no permitida por la matriz (US-C6) | 409 | `SCHEDULE_CONFLICT` |
| Zona horaria inexistente | 422 | `INVALID_METADATA` |
| Campo faltante o mal formado | 422 | `INVALID_METADATA` |
| `publishId` inexistente | 404 | `CONTENT_NOT_FOUND` |
| Falta el JWT, está vencido, mal firmado o sin `sub`/`exp` | 401 | `UNAUTHORIZED` |
| `POST /api/auth/token`: cliente inexistente, secreto incorrecto o sin credenciales | 401 | `UNAUTHORIZED` |
| `POST /api/auth/token`: `grant_type` distinto de `client_credentials` o faltante | 422 | `INVALID_METADATA` |

> **Transición inválida (US-C6).** `InvalidTransitionError` hereda de
> `DomainError` y reutiliza `SCHEDULE_CONFLICT` en su sentido de "conflicto de
> estado"; no se agregó un código a la lista registrada porque eso obliga a los
> otros tres equipos a actualizar su catálogo por un caso que hoy **no cruza la
> API**: la matriz protege rutas internas (el job handler y las futuras
> reconciliación y reintentos), no los endpoints. Si en el Sprint 4 el reintento
> manual de US-C7 necesita exponer este error al cliente, se propone el código
> nuevo por §5.4 en ese momento.

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
