# Conectar la cuenta de YouTube (OAuth 2.0) — US-C3

Cómo dejar el módulo autorizado para publicar en el canal. El porqué de
cada decisión está en `ADR/0008-oauth-youtube-consentimiento-tokens.md`.

Resumen: un administrador pide una URL de consentimiento, la abre en el
navegador con la cuenta dueña del canal y acepta. Desde ese momento el
módulo guarda un refresh token **cifrado** en PostgreSQL y lo usa para
publicar sin intervención.

## 1. Proyecto en Google Cloud (una vez por equipo)

En <https://console.cloud.google.com>, con el proyecto del equipo (el del
Sprint 0 sirve):

1. **Habilitar la API.** *APIs y servicios → Biblioteca →
   YouTube Data API v3 → Habilitar*.
2. **Pantalla de consentimiento.** Está en *APIs y servicios → Pantalla de
   consentimiento de OAuth*; la consola nueva la llama *Google Auth
   Platform*.
   - Tipo de usuario **Externo** y estado de publicación **Prueba**
     (*Testing*).
   - *Público → Usuarios de prueba*: agregar las cuentas de Google dueñas
     del canal. Solo ellas pueden dar el consentimiento.
   - *Acceso a los datos*: agregar el scope
     `https://www.googleapis.com/auth/youtube.upload`. Es el único que pide
     el módulo.
3. **Cliente OAuth.** *Clientes → Crear cliente*:
   - Tipo de aplicación: **Aplicación web**.
   - URI de redireccionamiento autorizados:
     `http://localhost:8000/oauth2/callback`, exacta, sin barra final.
   - Copiar el **ID de cliente** y el **secreto del cliente**. El secreto se
     comparte solo por un canal privado. Nunca va al repositorio.

Si ya tienen el `client_secret.json` del Sprint 0, los dos valores están en
su clave `"web"`. Revisen en la consola que el cliente sea de tipo web y que
tenga registrada esa URI. El archivo ya no se usa.

## 2. Configurar el módulo

Con `.env` creado desde `.env.example`, generen la clave que cifra los tokens:

```bash
docker compose run --rm --no-deps module3 \
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Completen en `.env`:

```bash
GOOGLE_CLIENT_ID=1234567890-xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
OAUTH_REDIRECT_URI=http://localhost:8000/oauth2/callback
OAUTH_TOKEN_ENCRYPTION_KEY=<la clave generada>
```

Y levanten el módulo con `docker compose up --build`. La migración `0002`,
que crea la tabla `oauth_credential`, corre sola.

> Si se pierde o se cambia `OAUTH_TOKEN_ENCRYPTION_KEY`, los tokens
> guardados no se pueden descifrar y hay que repetir el paso 3. No hay otro
> dato cifrado con esa clave.

## 3. Conectar la cuenta

Las rutas `/oauth2/*` piden un JWT con `role: admin`. En desarrollo:

```bash
TOKEN=$(docker compose exec -T module3 python scripts/generar_jwt.py --role admin)
```

En integración, registren un cliente con
`python scripts/generar_cliente.py --client-id operador --role admin` y pidan
el token a `POST /api/auth/token` (ADR-0007).

Pidan la URL de consentimiento:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/oauth2/authorize
```

La respuesta trae `data.authorizationUrl`. Desde Swagger es lo mismo:
<http://localhost:8000/docs>, botón *Authorize* con el token y
`POST /oauth2/authorize`.

1. Abran la URL en el navegador **antes de 10 minutos**. Sirve una sola vez.
2. Entren con la cuenta dueña del canal. Si administra varios canales,
   Google pregunta cuál: el módulo no puede saber cuál eligieron.
3. En modo Prueba, Google avisa que no verificó la app. Es normal: elijan
   *Continuar* y acepten el permiso para subir videos.
4. El navegador vuelve a `/oauth2/callback` y muestra `"connected": true`.

## 4. Verificar

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/oauth2/status
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/oauth2/refresh
```

`/oauth2/refresh` pide un access token nuevo con el mismo camino que usa el
publicador: `accessTokenExpiresAt` avanza una hora. Si eso funciona,
publicar no va a fallar por la autorización.

Para mostrar que los tokens no están en claro en la base:

```bash
docker compose exec postgres psql -U pubtube -d pubtube_m3 \
  -c "SELECT id, scopes, left(encrypted_tokens, 30) AS cifrado, expires_at FROM oauth_credential;"
```

## 5. Desconectar

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/oauth2/revoke
```

Revoca el acceso en Google y borra los tokens. Si Google no confirma la
revocación, responde 502 y conserva los tokens para que se pueda reintentar.

## Antes de cada demo

- **Reconecten la cuenta.** Con la app en modo Prueba, Google vence los
  refresh tokens a los 7 días. Una cuenta conectada hace más de una semana va
  a fallar con `OAUTH_ERROR`.
- **Los videos quedan privados.** YouTube restringe a privado lo que se sube
  desde proyectos de API que no pasaron su auditoría. No prometan un video
  público.

## Problemas frecuentes

| Síntoma | Causa | Qué hacer |
|---|---|---|
| Google muestra `redirect_uri_mismatch` | `OAUTH_REDIRECT_URI` no es idéntica a la registrada en el cliente | Igualarlas: esquema, puerto y barra final cuentan |
| Google bloquea el acceso porque la app no está verificada | La cuenta no es usuario de prueba | Agregarla en *Público → Usuarios de prueba* |
| Callback `400 OAUTH_ERROR`: "el state venció, ya se usó…" | Pasaron más de 10 min, se recargó la página del callback o el módulo se reinició en el medio | Pedir otra URL con `POST /oauth2/authorize` |
| `403 UNAUTHORIZED` en `/oauth2/*` | El JWT no trae `role: admin` | `generar_jwt.py --role admin` |
| `503 OAUTH_ERROR`: "OAuth sin configurar" | Faltan `GOOGLE_CLIENT_ID` o `GOOGLE_CLIENT_SECRET` | Completar `.env` y reiniciar |
| `503 OAUTH_ERROR` con `invalid_client` | Secreto mal copiado o cliente borrado en la consola | Revisar `.env` contra la consola |
| `503 OAUTH_ERROR`: "no se pueden descifrar" | Cambió `OAUTH_TOKEN_ENCRYPTION_KEY` | Restaurar la clave anterior o reconectar |
| `409 OAUTH_ERROR` con `invalid_grant`, y publicaciones fallidas con `OAUTH_ERROR` | El acceso venció (7 días) o lo revocó el dueño | Reconectar (paso 3) |
| `502 PUBLISH_FAILED` | Google no respondió | Reintentar. En las publicaciones el reintento es automático (ADR-0006) |
