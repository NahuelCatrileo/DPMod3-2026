# Contrato REST — Módulo 3 (Equipo C)

Versión 1 · Sprint 1 · Última actualización: 2026-10-23

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
| `POST /api/auth/register` | `{ displayName, email, password }` (alias de `displayName`: `display_name`, `username`) | `201 { status, data: cuenta }` | ADR-0009 |
| `POST /api/auth/login` | `{ email, password, remember? }` | `200 { access_token, token_type, expires_in }` + cookie `pubtube_refresh` | ADR-0009 |
| `GET /api/auth/me` | — (JWT con `kind: user`) | `200 { status, data: cuenta }` | ADR-0009 |
| `POST /api/auth/refresh` | cookie `pubtube_refresh` + cabecera `X-CSRF-Protection` | `200 { access_token, token_type, expires_in }` + cookie rotada | ADR-0009 |
| `POST /api/auth/logout` | cookie `pubtube_refresh` + cabecera `X-CSRF-Protection` | `204` + cookie borrada | ADR-0009 |
| `POST /api/publish/{id}/now` | — | **No implementado** (US-C7, Could, Sprint 4) | US-C7 |
| `POST /oauth2/authorize` | — (JWT con `role: admin`) | `200 { status, data: { authorizationUrl, expiresIn } }` | US-C3.3 |
| `GET /oauth2/callback` | query `state`, `code` o `error` (los pone Google) | `200 { status, data: conexión }` | US-C3.3 |
| `GET /oauth2/status` | — (JWT con `role: admin`) | `200 { status, data: conexión }` | US-C3 |
| `POST /oauth2/refresh` | — (JWT con `role: admin`) | `200 { status, data: conexión }` | US-C3.3 |
| `POST /oauth2/revoke` | — (JWT con `role: admin`) | `200 { status, data: conexión }` | US-C3.3 |

Todas las rutas `/api/publish/*` exigen la cabecera
`Authorization: Bearer <jwt>` (ADR-0005). `GET /api/health`,
`POST /api/auth/token`, `register`, `login`, `refresh` y `logout` quedan
públicos.

### Cuentas de usuario (ADR-0009)

La "cuenta" es `{ id, email, displayName, role, createdAt }`, con las
columnas de la sección 2 del issue #11 (`app_user`: `id` UUID,
`email_normalized`, `display_name`, `password_hash`, `role`, `is_active`).
Nunca incluye la contraseña ni su hash.

- `email`: hasta 255 caracteres. Se guarda sin espacios y en minúsculas, y es
  único.
- `displayName`: de 1 a 80 caracteres, sin espacios en los extremos. No es
  único. El registro también lo acepta como `display_name` o `username`, que
  es el campo del formulario de la UI.
- `password`: de 8 a 128 caracteres.
- `role`: lo fija la cuenta y no se elige al registrarse; los campos extra se
  ignoran. Es `user` salvo que se cambie en la base, por ejemplo a `admin`.

El login y el refresh responden con la misma forma que `POST /api/auth/token`
y `Cache-Control: no-store`. El JWT lleva `sub` (el UUID de la cuenta),
`role` (el de la cuenta), `kind: user`, `iat`, `exp`, `jti` e `iss` si se
configura. Los tokens de `POST /api/auth/token` llevan `kind: service`, y
ningún cliente de `AUTH_CLIENTS` puede tener `role: user`.

El refresh token viaja solo en la cookie
`pubtube_refresh; HttpOnly; Secure; SameSite=Strict; Path=/api/auth`, con
`Max-Age` solo si `remember` es `true`. Cada refresh lo rota, y presentar uno
ya rotado revoca toda la sesión. Un 401 de `refresh` también borra la cookie.

**CSRF.** `refresh` y `logout` exigen la cabecera `X-CSRF-Protection` con
cualquier valor (la UI manda `1`). Un formulario de otro sitio no puede
ponerla. Si la petición trae `Origin`, además tiene que estar en
`AUTH_ALLOWED_ORIGINS`. Si falta la cabecera o el origen no está permitido,
la respuesta es `403 UNAUTHORIZED` y la sesión no cambia. `register` y
`login` solo aceptan `application/json`.

**Qué ve cada token en `/api/publish/*`:**

| JWT | `POST /schedule` | `GET /{id}/status` |
|---|---|---|
| `kind: user` | Queda como dueño (`sub`) | Solo las suyas. La de otro: `404 CONTENT_NOT_FOUND` |
| `kind: user` con `role: admin` | Queda como dueño (`sub`) | Todas |
| `kind: service` | Queda como dueño (`sub`) | Todas, como antes |

Un token sin `kind`, emitido antes de ese claim, es de usuario si trae
`role: user` y de servicio en cualquier otro caso.

Las rutas de cuentas a través del gateway están en
[`../gateway-auth-usuarios.md`](../gateway-auth-usuarios.md).

Las rutas `/oauth2/*` conectan la cuenta de YouTube donde se publica
(ADR-0008). Son de operación del Equipo C: ningún otro módulo las llama, y
el gateway no las enruta. Exigen un JWT con `role: admin`, porque conectar
otra cuenta cambia el canal de todos los creadores. La excepción es
`GET /oauth2/callback`, que la llama el navegador redirigido por Google. Esa
ruta la protegen un `state` de un solo uso y PKCE.

La "conexión" es
`{ configured, connected, scopes, accessTokenExpiresAt, connectedAt, updatedAt }`.
Nunca incluye tokens. `configured` indica si están `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` y `OAUTH_TOKEN_ENCRYPTION_KEY`. Paso a paso de uso en
`docs/oauth-youtube.md`.

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

## Integración saliente con Módulo 1 (US-C3.4)

M3 no comparte base de datos con M1. Para publicar consulta los metadatos
versionados al endpoint REST del M1, usando la **URL interna** (no el Gateway):

```http
GET {MODULE1_BASE_URL}/api/content/{contentId}/metadata
Accept: application/json
X-Correlation-Id: <uuid generado por M3>
```

`MODULE1_BASE_URL` se configura en `.env` (por defecto
`http://module1-api:8000`) y `MODULE1_TIMEOUT_SECONDS` define el timeout.

Respuesta real del M1 (la que se parsea):

```json
{
  "status": 200,
  "contentId": "uuid-del-contenido",
  "metadata": {
    "id": "uuid-de-la-version",
    "version": 1,
    "title": "Clase 1",
    "description": "Descripción de la clase",
    "tags": ["pubtube", "clase"],
    "visibility": "unlisted",
    "createdAt": "2026-10-23T12:00:00.000Z"
  }
}
```

`metadata` puede ser `null` si el contenido todavía no tiene metadatos; en ese
caso M3 no publica y devuelve `INVALID_METADATA`.

### Dónde está usada

| Parte | Archivo | Qué hace |
|---|---|---|
| Cliente REST y parseo | `app/infra/module1.py` → `get_content_metadata()` | Llama al endpoint, valida la respuesta y la convierte en `ContentMetadata`. |
| Adaptador al publicador | `app/infra/publishers/youtube.py` → `content_from_m1()` | Combina esos metadatos con el archivo del video y arma `VideoContent`. |
| Uso por defecto | `YouTubePublisher.__init__()` | Si no se inyecta otro `content_provider`, usa `content_from_m1`. |
| Configuración | `app/config.py` | `MODULE1_BASE_URL`, `MODULE1_TIMEOUT_SECONDS`. |
| Pruebas | `tests/test_module1_content.py` | Parseo, errores HTTP y combinación con el archivo local. |

Mapeo de la respuesta de M1 a YouTube:

| M1 | YouTube (`videos.insert`) |
|---|---|
| `title` | `snippet.title` |
| `description` | `snippet.description` |
| `tags` | `snippet.tags` |
| `visibility` (`public`, `private`, `unlisted`) | `status.privacyStatus` |
| `version` | Se registra/valida; no se envía a YouTube |

Errores del M1 traducidos a códigos registrados:

| Respuesta M1 | Código M3 |
|---|---|
| `404` | `CONTENT_NOT_FOUND` |
| `400` / `422` | `INVALID_METADATA` |
| `401` / `403` | `UNAUTHORIZED` |
| `5xx`, timeout, red caída, JSON ilegible | `PUBLISH_FAILED` |

### Punto pendiente: el archivo del video

El endpoint `GET /api/content/{contentId}/metadata` **no devuelve el video**
ni su `storageUrl`. El M1 tampoco expone hoy una descarga o URL prefirmada de
lectura. Por eso `content_from_m1()` usa provisionalmente
`<YOUTUBE_VIDEO_DIR>/<contentId>.<ext>` y falla con `CONTENT_NOT_FOUND` si no
existe.

Para cerrar US-C3.4 falta acordar con M1 una de estas dos formas y
documentarla en este mismo archivo:

1. Un endpoint de descarga/URL prefirmada, por ejemplo
   `GET /api/content/{contentId}/video`.
2. Incluir `storageUrl` en la respuesta de metadatos o en
   `GET /api/content/{contentId}`, y que M3 tenga acceso al bucket.

Cuando exista, solo se reemplaza `_local_video_path()` en
`app/infra/publishers/youtube.py`; el cliente de metadatos y la subida a
YouTube no cambian.

> `metadata.updated` sigue sin incluir `description` (ver
> `docs/contratos/eventos.md`). M3 usa REST para obtenerla. Si más adelante se
> implementa el consumidor de ese evento, habrá que ampliar el contrato o
> mantener esta consulta REST para la descripción.

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
| `register`: correo ya registrado (sin importar mayúsculas ni espacios) | 409 | `DUPLICATE_CONTENT` |
| `register` / `login`: campo faltante o mal formado, o cuerpo que no es JSON | 422 | `INVALID_METADATA` |
| `refresh` / `logout`: sin `X-CSRF-Protection`, o `Origin` fuera de `AUTH_ALLOWED_ORIGINS` | 403 | `UNAUTHORIZED` |
| `login`: correo desconocido, contraseña incorrecta o cuenta desactivada | 401 | `UNAUTHORIZED` |
| `refresh`: sin cookie, o cookie desconocida, vencida, revocada o reutilizada | 401 | `UNAUTHORIZED` |
| `GET /api/auth/me` con un JWT válido que no es de usuario (`kind: service`) | 403 | `UNAUTHORIZED` |
| `GET /api/auth/me` de una cuenta borrada o desactivada | 401 | `UNAUTHORIZED` |
| `GET /api/publish/{id}/status` de otro usuario (token `role: user`) | 404 | `CONTENT_NOT_FOUND` |
| `/oauth2/*` con un JWT válido pero sin `role: admin` | 403 | `UNAUTHORIZED` |
| Callback con `state` desconocido, vencido o ya usado; consentimiento rechazado (`error=access_denied`); código rechazado por Google; sin `youtube.upload` o sin refresh token | 400 | `OAUTH_ERROR` |
| `POST /oauth2/refresh` sin cuenta conectada, o Google rechazó el refresh token (`invalid_grant`) | 409 | `OAUTH_ERROR` |
| OAuth sin configurar, cliente rechazado por Google (`invalid_client`) o tokens que la clave no descifra | 503 | `OAUTH_ERROR` |
| Google no respondió al canjear, refrescar o revocar | 502 | `PUBLISH_FAILED` |

> **403 con `UNAUTHORIZED` (ADR-0008).** La lista registrada no tiene un
> código para "autenticado pero sin permiso". El HTTP 403 lo distingue del
> 401, y así no hace falta proponer un código nuevo por §5.4 para rutas que
> solo usa el Equipo C. Por la misma razón, la caída de Google se informa con
> `PUBLISH_FAILED`, que la política de reintentos ya trata como transitorio.

> **Cuentas sin códigos nuevos (ADR-0009).** Un correo ya registrado usa
> `DUPLICATE_CONTENT`, que en la lista ya significa "eso ya existe". La
> publicación de otro usuario responde 404 y no 403: un 403 confirmaría que
> ese `publishId` existe. Si el Equipo D prefiere un código propio para
> cuentas, se propone por §5.4.

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
