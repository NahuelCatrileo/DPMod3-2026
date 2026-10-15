# ADR-0007 — Emisión de JWT a clientes de servicio (client credentials)

## Estado

Aceptada · 2026-10-10 · Sprint 2 (propuesta el 2026-10-08)

El equipo confirmó que el Módulo 3 emite los tokens. Complementa a ADR-0005,
que sigue vigente: el módulo sigue verificando el JWT en `/api/publish/*`
exactamente igual.

## Contexto

ADR-0005 asume que el API Gateway (Equipo D) emite los tokens. Pero ni el
backlog ni la guía técnica asignan esa tarea: US-D1 pide que el gateway
"valide JWT y rechace peticiones no autenticadas", no que los emita. Hasta
ahora solo existía `scripts/generar_jwt.py`, que sirve para pruebas manuales
y no para integración.

Al Módulo 3 lo llaman otros sistemas a través del gateway, no personas.
No hay usuarios ni contraseñas en este módulo.

## Alternativas consideradas

1. **Seguir solo con el script de desarrollo.** No resuelve la integración:
   alguien tiene que entregar tokens a los demás sistemas.
2. **Login de usuarios** (tabla de usuarios, contraseñas, refresh tokens,
   logout). Es la solución para personas; aquí agrega mucho código y una
   tabla nueva para un caso que el módulo no tiene.
3. **Client credentials de OAuth 2.0 (RFC 6749 §4.4).** Cada sistema
   registrado recibe un `client_id` y un `client_secret` y los cambia por un
   JWT de vida corta. Es el flujo estándar para comunicación entre servicios.

## Decisión

Alternativa 3.

- `POST /api/auth/token`, público, con el formulario
  `grant_type=client_credentials`, `client_id`, `client_secret`. Las
  credenciales también se aceptan por HTTP Basic. Si llegan por los dos
  lados, manda la cabecera.
- La respuesta sigue el estándar (`access_token`, `token_type`,
  `expires_in`, con `Cache-Control: no-store`) y no el sobre
  `{ status, data }`, para que cualquier cliente OAuth la entienda. Los
  errores sí usan el sobre acordado: `401 UNAUTHORIZED` por credenciales y
  `422 INVALID_METADATA` por un `grant_type` faltante o distinto. No se crean
  códigos nuevos.
- Los clientes se registran en `AUTH_CLIENTS` (JSON en `.env`) con el **hash**
  del secreto, PBKDF2-SHA256 con sal y 600 000 iteraciones, de la librería
  estándar. El secreto en claro solo lo ve quien corre
  `scripts/generar_cliente.py`.
- El token lleva `sub` (el `client_id`), `role` (configurable por cliente,
  `service` por defecto), `iat`, `exp`, `jti` y, si se configuran, `iss` y
  `aud`. Se firma con la misma `JWT_SECRET_KEY` que usa ADR-0005, así que el
  token emitido pasa la verificación del propio módulo.
- Vigencia por defecto de 30 minutos (`JWT_ACCESS_TOKEN_MINUTES`). No hay
  refresh token: el cliente pide uno nuevo cuando vence.
- Un `client_id` inexistente y un secreto incorrecto responden con el mismo
  mensaje y tardan lo mismo, para no revelar qué clientes existen.
- Con `APP_ENV=prod`, el módulo se niega a firmar si `JWT_SECRET_KEY` es uno
  de los valores de ejemplo (el default de `config.py` o el de
  `.env.example`) o mide menos de 32 bytes, el mínimo de HS256 (RFC 7518
  §3.2).

## Integración con el gateway

Verificado el 2026-10-10 contra el código de la imagen
`ghcr.io/tilininsano312/pubtube-mod4:develop` y con una prueba local
módulo → gateway → módulo (la misma que corre el CI):

- El gateway valida HS256 con `JWT_SECRET` y solo exige `exp`. Si falta
  `user_id` usa `sub`, así que no hace falta emitir `user_id`.
- El gateway reenvía la cabecera `Authorization` al módulo, que verifica el
  token otra vez. Por eso `JWT_SECRET` (gateway) y `JWT_SECRET_KEY` (este
  módulo) deben tener el mismo valor.
- El gateway no pasa `audience` a PyJWT, y PyJWT rechaza un token con `aud`
  en ese caso. `JWT_AUDIENCE` queda vacío mientras Equipo D no lo valide;
  `JWT_ISSUER` sí se puede usar.
- El gateway no tiene ruta para `POST /api/auth/token` y la exige
  autenticada, así que hoy los clientes piden el token directo al módulo.
- El gateway valida `iat` sin margen de reloj. Si su reloj va atrasado
  respecto del nuestro, un token recién emitido responde 401 durante esa
  diferencia.

## Consecuencias

- Este módulo pasa a ser el emisor de tokens del sistema: un token suyo vale
  para todas las rutas que el gateway protege con la misma clave, no solo
  para `/api/publish/*`.
- Con HS256, cualquiera que tenga la clave puede firmar tokens, también el
  gateway. Si eso deja de ser aceptable, el cambio es firmar con RS256: este
  módulo guarda la clave privada y el gateway solo la pública.
- Hay que acordar con Equipo D si el gateway expone `POST /api/auth/token`
  (ruta pública que reenvía al módulo) y si agrega margen de reloj al validar
  `iat`.
- Revocar un cliente es sacarlo de `AUTH_CLIENTS` y reiniciar. Los tokens ya
  emitidos siguen valiendo hasta su `exp`; por eso la vigencia es corta.
- No hay límite de intentos en el endpoint. US-D1 pide rate limiting en el
  gateway, pero mientras el token se pida directo al módulo ese límite no lo
  cubre.
- El CI de integración pide un token a este módulo y lo usa a través del
  gateway, así que detecta si el gateway cambia lo que exige del token.
- Dependencia nueva: `python-multipart`, que FastAPI necesita para leer
  formularios.
