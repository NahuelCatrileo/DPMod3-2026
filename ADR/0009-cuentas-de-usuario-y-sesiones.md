# ADR-0009 — Cuentas de usuario, sesiones con refresh token y dueño de cada publicación

## Estado

Propuesta · 2026-10-22 · Sprint 3 (issue #11, abierto por el Módulo 4)

Complementa a ADR-0007: la alternativa 2 de esa decisión (login de usuarios),
descartada porque al módulo no lo llamaban personas, se implementa ahora. El
flujo *client credentials* no cambia. Complementa a ADR-0005: la verificación
del JWT es la misma, y se agrega qué publicaciones ve cada tipo de token.

Revisada el 2026-10-28, cuando el Módulo 4 detalló el issue #11. La tabla de
usuarios pasa a las columnas de su sección 2 (migración `0004`), se agrega el
claim `kind` para separar personas de sistemas, y `refresh` y `logout` exigen
una cabecera contra CSRF.

## Contexto

El Módulo 4 abrió el issue #11 pidiendo registro, login y sesiones de
usuarios. Su frontend (`PubTube-ui`, rama `feature/auth-views`) ya tiene las
vistas de login (`email`, `password`, `remember`) y de registro (`username`,
`email`, `password`) esperando un contrato.

Este módulo ya emite los JWT del sistema (ADR-0007) y el gateway los valida
con la misma clave. Hasta ahora todo token era de un sistema y veía todas las
publicaciones: con personas, una tiene que poder ver las suyas y no las de
otra.

## Alternativas consideradas

1. **Que las cuentas vivan en el gateway (Módulo 4).** El gateway no tiene
   base de datos, y tendría que firmar tokens con la clave que hoy solo usa
   para verificar. Quedarían dos emisores de JWT para el mismo sistema.
2. **Solo "Continuar con Google".** La UI tiene el botón, pero el issue pide
   correo y contraseña. Queda como posible extensión: no cambia nada de lo
   que se decide aquí.
3. **Cuentas en este módulo, que ya es el emisor.** Tabla de usuarios en su
   PostgreSQL y la misma firma que los tokens de servicio.

Para la sesión:

- **a. Refresh token en el cuerpo de la respuesta**, guardado por el
  frontend en `localStorage`. Lo lee cualquier script de la página: un XSS se
  lleva una sesión que dura días.
- **b. Refresh token en una cookie HttpOnly**, con el JWT de acceso en el
  cuerpo y en memoria. El script no puede leer la cookie, y lo que roba un
  XSS dura lo que dura el JWT.

## Decisión

Alternativa 3 con sesión **b**.

**Cuentas**

- Tabla `app_user` con las columnas de la sección 2 del issue: `id` UUID,
  `email_normalized` VARCHAR(255) único, `display_name` VARCHAR(80),
  `password_hash` TEXT, `role` VARCHAR(30) con `user` por defecto,
  `is_active`, `created_at` y `updated_at`. La creó la migración `0003` y la
  `0004` la llevó a estas columnas sin perder las cuentas ni las sesiones.
  `role` es NOT NULL, aunque la tabla del issue no lo exige: un rol vacío no
  se puede poner en un JWT.
- El correo se guarda sin espacios y en minúsculas, y es lo único que
  identifica la cuenta. `display_name` es solo para mostrar y puede
  repetirse. El registro lo recibe como `displayName`, con `display_name` y
  `username` como alias: `username` es el campo del formulario de la UI.
- La contraseña se guarda como hash **Argon2id** con el perfil de baja
  memoria de RFC 9106 (64 MiB, 3 pasadas, 4 hilos), con `argon2-cffi`. Si los
  parámetros cambian, el hash se actualiza en el siguiente login.
- Contraseña de 8 a 128 caracteres: el mínimo es el del formulario de la
  UI; el máximo evita que alguien mande megas para que Argon2id los procese.
- El rol no se elige al registrarse: toda cuenta nace con `role: user`, y los
  campos extra del cuerpo se ignoran. Otro rol se asigna en la base.
- Un correo sin cuenta tarda lo mismo que una contraseña incorrecta (se
  verifica contra un hash de relleno) y responde el mismo 401.

**Tokens**

- El JWT de usuario se firma con la misma función que el de servicio
  (`app/services/tokens.py`): mismos claims (`sub`, `role`, `kind`, `iat`,
  `exp`, `jti`, `iss`), misma clave, misma vigencia
  (`JWT_ACCESS_TOKEN_MINUTES`) y misma guarda de producción. `sub` es el
  UUID de la cuenta y `role` es el de la columna.
- **`kind` separa personas de sistemas.** Vale `user` en los tokens del
  login y `service` en los de `POST /api/auth/token`. `role` dice qué puede
  hacer el titular; `kind`, qué es. Antes la diferencia dependía solo de
  `role`, y un cliente de `AUTH_CLIENTS` configurado con `role: user` se
  habría hecho pasar por persona. Ahora la configuración rechaza ese rol en
  los clientes. Los tokens emitidos antes de `kind` siguen funcionando: si
  traen `role: user` cuentan como usuario, y si no, como servicio.
- El refresh token es un valor opaco de 256 bits en la cookie
  `pubtube_refresh`, con `HttpOnly`, `Secure`, `SameSite=Strict` y
  `Path=/api/auth`. En la tabla `refresh_token` solo queda su SHA-256; no
  hace falta un hash lento porque no es una contraseña elegida por alguien.
- Cada `POST /api/auth/refresh` revoca el token con un UPDATE condicional y
  entrega otro de la misma familia. Si llega un token ya revocado, se revoca
  la familia entera: alguien lo copió.
- "Recordar sesión" hace la cookie persistente (`Max-Age` de
  `AUTH_REFRESH_TOKEN_DAYS`, 7 por defecto). Sin marcarlo, es una cookie de
  sesión del navegador. El servidor la vence a los mismos días en los dos
  casos, y cada refresh reinicia el plazo.
- `POST /api/auth/logout` revoca la familia de esa cookie, no las de otros
  dispositivos, y no exige JWT: el de la sesión puede haber vencido.

**CSRF**

`refresh` y `logout` se autentican con la cookie, que el navegador manda
solo. Por eso exigen dos cosas:

- La cabecera `X-CSRF-Protection`, con cualquier valor. Un formulario de otro
  sitio no puede ponerla, y un `fetch` desde otro origen la anuncia en un
  preflight que solo pasa si el gateway acepta ese origen.
- Si la petición trae `Origin`, que esté en `AUTH_ALLOWED_ORIGINS`. Un
  cliente que no es navegador no manda `Origin` y pasa solo con la cabecera.

Si falla cualquiera de las dos, la respuesta es 403 y la sesión no cambia.
`register` y `login` no necesitan la cabecera: solo aceptan
`application/json`, que un formulario de otro sitio no puede mandar.

Se descartó el *double submit cookie*, que guarda el token CSRF en una
cookie legible por JavaScript. Si la UI queda en otro dominio que el gateway,
no puede leer las cookies del gateway, y tras recargar la página no tendría
con qué renovar la sesión.

**Permisos**

- `publication.owner_id` guarda el `sub` del token que la programó.
- Con un token `kind: user`, `GET /api/publish/{id}/status` solo encuentra
  las publicaciones propias, salvo que la cuenta tenga `role: admin`. La de
  otro responde **404 `CONTENT_NOT_FOUND`**, igual que una inexistente: un
  403 confirmaría que el `publishId` existe. El 409 por contenido ya
  programado tampoco muestra el `publishId` ajeno.
- Los tokens de servicio siguen viendo todas las publicaciones, como
  establece ADR-0005. Las rutas `/oauth2/*` piden `role: admin` (ADR-0008),
  así que una cuenta común no las pasa.

**Códigos de error.** No se agregan códigos (§5.4). Un correo ya
registrado responde `409 DUPLICATE_CONTENT`, con el mismo sentido de "eso ya
existe". Credenciales o sesión inválidas responden `401 UNAUTHORIZED`. Un
token de servicio en `/api/auth/me`, o un `refresh`/`logout` que no pasa el
control de CSRF, responden `403 UNAUTHORIZED`, como en ADR-0008.

## Consecuencias

- El gateway tiene que publicar `register`, `login`, `refresh` y `logout`
  sin JWT y enrutar `/api/auth/me`. El parche, probado contra su suite y
  extremo a extremo, está en `docs/gateway-auth-usuarios.md`. Mientras no
  lo integren, la cuenta y la sesión se piden directo al módulo y el JWT de
  usuario sí funciona a través del gateway. El CI prueba las dos cosas.
- La cookie es del dominio del gateway. Si la UI se sirve desde otro sitio,
  el gateway necesita CORS con credenciales que acepte `X-CSRF-Protection`,
  y este módulo `AUTH_COOKIE_SAMESITE=none` y `AUTH_ALLOWED_ORIGINS`. Lo
  recomendado es servir la UI y el gateway bajo el mismo dominio detrás de
  NGINX, con HTTPS.
- El logout no invalida el JWT ya emitido: vale hasta su `exp`, como máximo
  30 minutos. Es la misma condición que ADR-0007 acepta para los servicios.
- Dos pestañas que refrescan a la vez con la misma cookie: la segunda se ve
  como reuso y cierra la sesión. El frontend debe hacer un solo refresh a la
  vez.
- `POST /api/auth/register` revela con su 409 si un correo tiene cuenta.
  Evitarlo exige verificar el correo por email, que no está en el alcance.
  Lo mismo pasa con la recuperación de contraseña: la UI tiene el enlace,
  pero no hay endpoint.
- No hay bloqueo por intentos fallidos en el módulo. Contra fuerza bruta
  cuentan el rate limiting del gateway (US-D1) y el costo de Argon2id: cada
  intento cuesta 64 MiB de memoria.
- Este módulo no comprueba que el `contentId` sea del usuario: eso lo sabe el
  Módulo 1. Un usuario puede programar un contenido ajeno si conoce su id,
  aunque después solo verá su propia publicación.
- Las publicaciones creadas antes de la migración quedan con `owner_id`
  NULL: solo las ven los servicios.
- Las filas revocadas o vencidas de `refresh_token` no se borran. Cuando
  pesen, basta con un job que las limpie.
- El downgrade de `0004` a `0003` vuelve a exigir `username` único: si dos
  cuentas comparten `display_name`, falla sin cambiar nada (PostgreSQL
  revierte todo el DDL de la migración).
- Dependencia nueva: `argon2-cffi`.
