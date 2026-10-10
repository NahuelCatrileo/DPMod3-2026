# ADR-0007 — Emisión de JWT a clientes de servicio (client credentials)

## Estado

Propuesta · 2026-10-08 · Sprint 2

Complementa a ADR-0005, que sigue vigente: el módulo sigue verificando el
JWT en `/api/publish/*` exactamente igual. Solo se usa si en la reunión de
integración se acuerda que el Módulo 3 emite los tokens.

## Contexto

ADR-0005 asume que el API Gateway (Equipo D) emite los tokens. Pero ni el
backlog ni la guía técnica asignan esa tarea: US-D1 pide que el gateway
"valide JWT y rechace peticiones no autenticadas", no que los emita.
Mientras eso no se acuerde, solo existe `scripts/generar_jwt.py`, que sirve
para pruebas manuales y no para integración.

Al Módulo 3 lo llaman otros sistemas (el gateway, el Módulo 4), no personas.
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
- Con `APP_ENV=prod`, el módulo se niega a firmar si `JWT_SECRET_KEY` sigue
  con el valor por defecto.

## Consecuencias

- Hay que confirmar con Equipo D quién emite los tokens. Si el gateway los
  emite, `AUTH_CLIENTS` queda vacío y el endpoint no entrega tokens a nadie.
- Si este módulo los emite y el gateway los valida, los dos deben usar la
  misma clave y algoritmo, y acordar qué claims espera el gateway. El CI de
  integración firma sus tokens de prueba con `sub`, `user_id` y `role`; este
  endpoint no emite `user_id`.
- Revocar un cliente es sacarlo de `AUTH_CLIENTS` y reiniciar. Los tokens ya
  emitidos siguen valiendo hasta su `exp`; por eso la vigencia es corta.
- No hay límite de intentos en el endpoint. US-D1 pide rate limiting en el
  gateway; si el endpoint queda expuesto sin gateway, hay que agregarlo.
- Dependencia nueva: `python-multipart`, que FastAPI necesita para leer
  formularios.
