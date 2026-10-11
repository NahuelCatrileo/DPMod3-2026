# ADR-0005 — Autenticación de la API del módulo con JWT

## Estado

Propuesta · 2026-10-06 · Sprint 2

Complementada por ADR-0007 (2026-10-10): los tokens no los emite el gateway
sino este módulo, en `POST /api/auth/token`. La verificación descrita aquí no
cambia.

No reemplaza el OAuth 2.0 de Google (US-C3), que sigue siendo obligatorio
para publicar en YouTube.

## Contexto

Hasta ahora los endpoints `/api/publish/*` no exigían ninguna credencial:
cualquiera que alcanzara el puerto del módulo podía programar publicaciones.
La única "verificación" del repositorio era el flujo OAuth de Google en
`app/oauth/`, que sirve para otra cosa: obtener permiso para subir videos a
una cuenta de YouTube, no para identificar a quien llama a nuestra API.

La documentación del curso fija el stack de autenticación como
"OAuth 2.0 + JWT (PyJWT)" (Doc 1, tabla de stack), indica que "la
autenticación se valida en el Gateway (JWT)" (Doc 3 §5) y reserva el código
`UNAUTHORIZED` en la lista de errores acordados. El gateway y la emisión de
tokens son de Equipo D (US-D1).

## Alternativas consideradas

1. **Confiar solo en el gateway.** El módulo no valida nada y asume que toda
   petición viene del gateway. Es lo más simple, pero si el contenedor queda
   expuesto (por ejemplo, el puerto 8000 publicado en `docker-compose`), la
   API queda abierta.
2. **Reemplazar OAuth por JWT.** Descartada: YouTube Data API solo acepta
   OAuth 2.0 de Google para subir videos, así que US-C3 no se podría cumplir.
3. **Llevar el `state` y el `code_verifier` del flujo OAuth en un JWT** en vez
   de la cookie de sesión. Cambia un detalle interno del flujo con Google,
   pero deja la API igual de abierta.
4. **Verificar también en el módulo el JWT que emite el gateway.** Dependencia
   de FastAPI sobre el router de publicación, con PyJWT y clave compartida.

## Decisión

Alternativa 4.

- Las rutas `/api/publish/*` exigen `Authorization: Bearer <jwt>`.
  `GET /api/health` y las rutas `/oauth2/*` quedan fuera: la primera la usan
  las sondas de salud y las segundas son redirecciones del navegador hacia
  Google.
- Firma HS256 con `JWT_SECRET_KEY`, que se comparte con Equipo D por un canal
  privado y vive solo en `.env`. Se pasa a PyJWT una lista cerrada de
  algoritmos, así un token con `alg: none` se rechaza.
- Claims obligatorios: `exp` y `sub`. `iss` y `aud` se exigen solo si se
  configuran `JWT_ISSUER` y `JWT_AUDIENCE`. Tolerancia de reloj de 30 s.
- Cualquier fallo responde `401` con el sobre acordado
  (`code: UNAUTHORIZED`) y la cabecera `WWW-Authenticate: Bearer`. El motivo
  concreto va al log, no a la respuesta.
- `scripts/generar_jwt.py` emite tokens de prueba mientras el gateway no esté
  integrado.

## Consecuencias

- Hay que acordar con Equipo D, en la reunión de integración, el algoritmo,
  la clave, y los valores de `iss`/`aud` si se usan. Si el gateway firma con
  RS256, basta con cambiar `JWT_ALGORITHM` y poner la clave pública en
  `JWT_SECRET_KEY`.
- Quien llame a la API directamente (Equipo D desde el gateway, pruebas
  manuales, Swagger) necesita un token. En `/docs` aparece el botón
  Authorize.
- Las pruebas usan el fixture `client` ya autenticado; `anon_client` sirve
  para probar el 401.
- El job del scheduler no pasa por HTTP, así que no necesita token.
