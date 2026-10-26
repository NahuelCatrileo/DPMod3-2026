# ADR-0008 — OAuth 2.0 con Google para publicar en YouTube: consentimiento, refresh y tokens cifrados

## Estado

Propuesta · 2026-10-22 · Sprint 3 (US-C3.1, US-C3.2 y US-C3.3)

Complementa a ADR-0005: de las rutas `/oauth2/*`, que esa decisión dejaba
públicas, solo `GET /oauth2/callback` lo sigue siendo. Usa la política de
reintentos de ADR-0006 sin agregar códigos.

## Contexto

US-C3 pide publicar con la API real de YouTube. `videos.insert` solo acepta
OAuth 2.0 de Google con el permiso de la cuenta dueña del canal (ADR-0005,
alternativa 2). El módulo publica sin nadie frente a la pantalla: el
scheduler dispara a cualquier hora. Necesita acceso *offline*, es decir, un
refresh token para pedir access tokens nuevos (duran una hora) sin volver a
pedir el consentimiento.

ADR-0002 dejaba el código OAuth del Sprint 0 en `app/oauth/` "listo para
Sprint 3". Al revisarlo aparecieron cuatro problemas:

1. **El flujo nunca terminaba.** `Flow.from_client_secrets_file` no genera
   PKCE si no se le pide (`autogenerate_code_verifier`). `code_verifier`
   quedaba en `None` y el callback respondía siempre 400.
2. **El callback no validaba `state`.** El `Flow` del callback no conocía el
   `state` original y oauthlib solo lo compara si lo tiene. Eso deja el
   callback abierto a CSRF: se le puede hacer guardar los tokens de otra
   cuenta.
3. **Cualquiera podía iniciar el consentimiento.** `/oauth2/*` era pública
   (ADR-0005). Quien autorizara su propia cuenta reemplazaba la del canal, y
   desde ese momento los videos de los creadores se subirían a la suya.
4. **El refresh token quedaba en texto plano** en `credentials/token.json`,
   dentro del contenedor. Ese archivo se pierde en cada
   `docker compose up --build` (el contenedor se recrea y no tiene volumen),
   y mientras existe, quien lo lea puede subir videos al canal.

Además, las credenciales del cliente salían de `client_secret.json`, que
`.dockerignore` deja fuera de la imagen. En Docker el flujo no tenía de
dónde leerlas.

## Alternativas consideradas

### De dónde salen las credenciales del cliente OAuth (US-C3.1)

1. **`client_secret.json` (lo que había).** Hay que montarlo en el
   contenedor, y es un archivo más que se puede versionar por error.
2. **Variables de entorno `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET`.** Igual
   que el resto de la configuración (12-factor) y que los otros secretos del
   módulo (`JWT_SECRET_KEY`, `AUTH_CLIENTS`).

### Dónde y cómo guardar los tokens (US-C3.2)

1. **`token.json` en claro (lo que había).** Se pierde al recrear el
   contenedor y lo puede leer cualquiera con acceso al filesystem o a un
   respaldo.
2. **Archivo cifrado en un volumen de Docker.** Resuelve la
   confidencialidad, pero agrega un volumen y un segundo lugar con estado
   además de PostgreSQL.
3. **Tabla en PostgreSQL con los tokens cifrados.** El volumen `pg_data` ya
   existe y sobrevive a los rebuild, y la base ya es la fuente de verdad del
   módulo (ADR-0004). Un dump o un respaldo de la base no alcanza para
   publicar sin la clave, que vive solo en `.env`.
4. **Gestor de secretos (Google Secret Manager, Vault).** Es lo indicado en
   producción, pero agrega infraestructura y credenciales de servicio que el
   curso no tiene.

### Quién puede iniciar el consentimiento (US-C3.3)

1. **Cualquiera (lo que había).** Descartada por el problema 3.
2. **HTTP Basic con un cliente de `AUTH_CLIENTS`.** Funciona directo en el
   navegador, que pide usuario y clave. Pero mezcla las credenciales de
   servicio de ADR-0007 con una persona que opera, y el navegador recuerda la
   clave sin forma de cerrar sesión.
3. **JWT con `role: admin` y el `state` guardado en el servidor.** Reusa
   ADR-0005 y ADR-0007. Como un navegador no envía `Authorization: Bearer`
   al navegar, la ruta devuelve la URL de Google en vez de redirigir. Quien
   pide la URL (curl, Swagger) puede no ser el navegador que vuelve al
   callback, así que `state` y `code_verifier` se guardan en el servidor y
   no en la cookie de sesión.

## Decisión

Alternativa 2 para la configuración, 3 para los tokens y 3 para el
consentimiento.

**Cliente y flujo (US-C3.1)**

- Cliente OAuth de tipo *Aplicación web*, con
  `http://localhost:8000/oauth2/callback` como URI de redirección.
  `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET` van en `.env`. Paso a paso en
  `docs/oauth-youtube.md`.
- Flujo *authorization code* con PKCE S256 (RFC 7636), `access_type=offline`
  y `prompt=consent`. Google entrega el refresh token solo en el primer
  consentimiento; con `prompt=consent` lo entrega siempre, así reconectar
  funciona. No se pide `include_granted_scopes`.
- Un único scope, `youtube.upload`: es el que exige `videos.insert`.
- El código se canjea pasando `code` y no la URL del callback. Así oauthlib no
  exige HTTPS en `http://localhost`, y no hace falta
  `OAUTHLIB_INSECURE_TRANSPORT`.

**Consentimiento (US-C3.3)**

- `POST /oauth2/authorize` exige un JWT con `role: admin` y devuelve
  `{ authorizationUrl, expiresIn }`.
- `state` (30 caracteres aleatorios, unos 178 bits) y `code_verifier` quedan
  en memoria del servidor. Valen 10 minutos y sirven una sola vez.
- `GET /oauth2/callback` es la única ruta pública, porque la llama el
  navegador que redirige Google. Rechaza un `state` desconocido, vencido o
  usado. Respeta `error=access_denied`. Canjea con el `code_verifier` y exige
  que se haya otorgado `youtube.upload` y que venga el refresh token.
- Rutas de operación, también con JWT admin: `GET /oauth2/status`,
  `POST /oauth2/refresh` (fuerza un refresh para comprobar que el acceso
  sigue vivo) y `POST /oauth2/revoke` (revoca en Google y borra).

**Almacenamiento (US-C3.2)**

- Tabla `oauth_credential` (migración `0002`), una fila por cuenta (hoy solo
  `youtube`).
- Refresh token y access token se cifran juntos con Fernet (AES-128-CBC +
  HMAC-SHA256, librería `cryptography`) y la clave
  `OAUTH_TOKEN_ENCRYPTION_KEY`. Si la fila se altera, el HMAC no coincide y
  se rechaza.
- En claro quedan solo los scopes y las fechas. El `client_secret` no se
  guarda: al refrescar sale de la configuración.
- Ningún token va al log ni a una respuesta HTTP.

**Refresh (US-C3.3)**

`app/oauth/credentials.py::get_credentials()` es lo que llama el publicador
antes de usar la API. Refresca cuando al access token le quedan menos de
3 min 45 s (el umbral de google-auth) y guarda el token nuevo. Los fallos se
traducen a la política de ADR-0006:

| Situación | Código | Clase | HTTP en `/oauth2/*` |
|---|---|---|---|
| No hay cuenta conectada | `OAUTH_ERROR` | definitivo | 409 |
| `invalid_grant`: acceso revocado o vencido. Se borra la credencial | `OAUTH_ERROR` | definitivo | 409 |
| Cliente rechazado (`invalid_client`) o tokens que la clave no descifra | `OAUTH_ERROR` | definitivo | 503 |
| Google no responde o devuelve 5xx | `PUBLISH_FAILED` | transitorio | 502 |

Un lock del proceso serializa refresh, consentimiento y revocación. Así dos
jobs que publican a la vez no refrescan dos veces, y un refresh lento no
vuelve a escribir tokens viejos sobre un consentimiento nuevo o sobre una
revocación.

Se retiran `SessionMiddleware`, `SESSION_SECRET_KEY` e `itsdangerous`: el
flujo ya no usa la cookie de sesión.

## Consecuencias

- **Refresh tokens de 7 días en modo Testing.** Mientras la pantalla de
  consentimiento esté en "Testing" con usuarios externos, Google vence los
  refresh tokens a los 7 días. Hay que reconectar la cuenta antes de cada
  demo. Pasar la app a "En producción" evita el vencimiento, pero sin la
  verificación de Google la pantalla de consentimiento muestra una
  advertencia de app no verificada. El vencimiento llega como
  `invalid_grant`: las publicaciones fallan con `OAUTH_ERROR` y
  `GET /oauth2/status` pasa a `connected: false`.
- **Videos privados.** Según la documentación de `videos.insert`, lo que se
  sube desde un proyecto de API no auditado (creado después del 28-07-2020)
  queda restringido a privado. Afecta la demo de US-C3: hay que declararlo y
  no prometer un video público.
- Mientras la app esté en Testing, solo los usuarios de prueba del proyecto
  pueden dar el consentimiento.
- Si se cambia o se pierde `OAUTH_TOKEN_ENCRYPTION_KEY`, los tokens guardados
  quedan ilegibles y hay que volver a autorizar. No se pierde nada más: la
  clave solo cifra esos tokens.
- El `state` pendiente vive en memoria. Si el módulo se reinicia entre el
  pedido de la URL y la vuelta de Google, hay que pedir la URL de nuevo. Como
  el lock, supone una sola instancia (la misma limitación de ADR-0003).
- No se pide `youtube.readonly`, así que el módulo no puede decir qué canal
  quedó conectado. Quien autoriza tiene que elegir la cuenta correcta en la
  pantalla de Google. `GET /oauth2/status` informa scopes y fechas.
- Para operar hace falta un JWT con `role: admin`:
  `scripts/generar_cliente.py --role admin`, o
  `scripts/generar_jwt.py --role admin` en desarrollo.
- `cryptography` pasa a ser dependencia explícita (ya llegaba con
  google-auth).
- `app/oauth/` deja de estar excluido de la cobertura.
- Queda para el resto de US-C3, la subida con `videos.insert`: llamar a
  `get_credentials()` antes de cada subida y traducir sus excepciones con
  `PublishResult.failure(exc.code, exc.message)`.
