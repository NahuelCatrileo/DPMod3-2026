# ADR-0009 — Cuentas de usuario, sesiones con refresh token y dueño de cada publicación

## Estado

Propuesta · 2026-10-22 · Sprint 3 (issue #11, abierto por el Módulo 4)

Complementa a ADR-0007: la alternativa 2 de esa decisión (login de usuarios),
descartada porque al módulo no lo llamaban personas, se implementa ahora. El
flujo *client credentials* no cambia. Complementa a ADR-0005: la verificación
del JWT es la misma, y se agrega qué publicaciones ve cada rol.

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

- Tabla `app_user` (migración `0003`). Correo y nombre de usuario en
  minúsculas y únicos. La contraseña se guarda como hash **Argon2id** con el
  perfil de baja memoria de RFC 9106 (64 MiB, 3 pasadas, 4 hilos), con
  `argon2-cffi`. Si los parámetros cambian, el hash se actualiza en el
  siguiente login.
- Contraseña de 8 a 128 caracteres: el mínimo es el del formulario de la
  UI; el máximo evita que alguien mande megas para que Argon2id los procese.
- El rol no se elige al registrarse: todo usuario recibe `role: user`, y los
  campos extra del cuerpo se ignoran.
- Un correo sin cuenta tarda lo mismo que una contraseña incorrecta (se
  verifica contra un hash de relleno) y responde el mismo 401.

**Tokens**

- El JWT de usuario se firma con la misma función que el de servicio
  (`app/services/tokens.py`): mismos claims (`sub`, `role`, `iat`, `exp`,
  `jti`, `iss`), misma clave, misma vigencia (`JWT_ACCESS_TOKEN_MINUTES`) y
  misma guarda de producción. `sub` es el id del usuario y `role` es `user`.
  El gateway lo valida sin cambios.
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

**Permisos**

- `publication.owner_id` guarda el `sub` del token que la programó.
- Con `role: user`, `GET /api/publish/{id}/status` solo encuentra las
  publicaciones propias. La de otro responde **404 `CONTENT_NOT_FOUND`**,
  igual que una inexistente: un 403 confirmaría que el `publishId` existe.
  El 409 por contenido ya programado tampoco muestra el `publishId` ajeno.
- Los tokens de servicio y de admin siguen viendo todas las publicaciones,
  como establece ADR-0005. Un token de usuario no pasa las rutas `/oauth2/*`,
  que piden `role: admin` (ADR-0008).

**Códigos de error.** No se agregan códigos (§5.4). Un correo o nombre de
usuario ya registrado responde `409 DUPLICATE_CONTENT`, con el mismo sentido
de "eso ya existe". Credenciales o sesión inválidas responden
`401 UNAUTHORIZED`, y un token de servicio en `/api/auth/me` responde
`403 UNAUTHORIZED`, como en ADR-0008.

## Consecuencias

- El gateway tiene que publicar `register`, `login`, `refresh` y `logout`
  sin JWT y enrutar `/api/auth/me`. El parche, probado contra su suite y
  extremo a extremo, está en `docs/gateway-auth-usuarios.md`. Mientras no
  lo integren, la cuenta y la sesión se piden directo al módulo y el JWT de
  usuario sí funciona a través del gateway. El CI prueba las dos cosas.
- La cookie es del dominio del gateway. Si la UI se sirve desde otro sitio,
  el gateway necesita CORS con credenciales, y este módulo
  `AUTH_COOKIE_SAMESITE=none`. Lo recomendado es servir la UI y el gateway
  bajo el mismo dominio detrás de NGINX.
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
- Dependencia nueva: `argon2-cffi`.
