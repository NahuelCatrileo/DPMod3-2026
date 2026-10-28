# Sesiones de usuario a través del gateway (issue #11)

Para el Equipo D (Módulo 4). Qué ofrece el Módulo 3, qué cambia en el
gateway, qué necesita el frontend y cómo comprobar el flujo de extremo a
extremo. La decisión y sus motivos están en
[ADR-0009](../ADR/0009-cuentas-de-usuario-y-sesiones.md); el contrato
completo, en [`contratos/rest.md`](contratos/rest.md).

## Qué expone el Módulo 3

| Ruta | Pide | Responde |
|---|---|---|
| `POST /api/auth/register` | JSON `{ displayName, email, password }` | `201 { status, data: cuenta }` |
| `POST /api/auth/login` | JSON `{ email, password, remember? }` | `200 { access_token, token_type, expires_in }` y la cookie `pubtube_refresh` |
| `GET /api/auth/me` | `Authorization: Bearer <jwt de usuario>` | `200 { status, data: cuenta }` |
| `POST /api/auth/refresh` | la cookie `pubtube_refresh` y `X-CSRF-Protection: 1` | `200 { access_token, ... }` y la cookie rotada |
| `POST /api/auth/logout` | la cookie `pubtube_refresh` y `X-CSRF-Protection: 1` | `204` y la cookie borrada |

- **`cuenta`** es `{ id, email, displayName, role, createdAt }` y sale de la
  tabla `app_user`, con las columnas de la sección 2 del issue.
- **Alias del registro:** acepta `display_name` y `username` en lugar de
  `displayName`, así el formulario actual de la UI funciona sin cambios.
- **El JWT de usuario** lleva `sub` (el UUID de la cuenta), `role` (el de la
  cuenta, `user` por defecto) y `kind: user`.
- **Los tokens de `POST /api/auth/token`** llevan `kind: service`. Los dos
  tipos se firman con la misma clave, así que el gateway los valida igual.
- **La cookie** es `HttpOnly; Secure; SameSite=Strict; Path=/api/auth`. Va
  sin `Domain`, así que el navegador la asocia al host que respondió, es
  decir, al gateway.

## Cambios en el gateway (PR separada)

Verificados contra `develop` (`7b8b2b3`). Con el parche pasan los 108 tests
del gateway (103 que ya existían y 5 nuevos), también con
`ruff --select E4,E7,E9,F`. El flujo completo de abajo funciona contra una
imagen construida con él.

### 1. Middleware

- **Rutas públicas exactas.** `register`, `login`, `refresh` y `logout` las
  llama alguien que todavía no tiene JWT, o que solo tiene la cookie.
  `/api/auth/me` sigue protegida, y el resto de `/api/auth/*` también.
- **`sub` obligatorio,** además de `exp`, firma y algoritmo.
- **`request.state.kind`** queda disponible para que el gateway aplique
  controles propios a usuarios, por ejemplo un rate limit por `sub`.

```diff
--- a/gateway/app/middleware/jwt_auth.py
+++ b/gateway/app/middleware/jwt_auth.py
@@ PUBLIC_PATHS = frozenset(
         "/api/auth/token",
+        # Module 3 user sessions (issue #11): the caller has no JWT yet, or
+        # only holds the HttpOnly refresh cookie. /api/auth/me stays protected.
+        "/api/auth/register",
+        "/api/auth/login",
+        "/api/auth/refresh",
+        "/api/auth/logout",
@@ class JWTAuthenticationMiddleware(BaseHTTPMiddleware):
         request.state.role = claims.get("role")
+        request.state.kind = _token_kind(claims)
@@ def _decode_token(token: str) -> dict[str, Any] | None:
             options={
-                "require": ["exp"],
+                # Every caller must identify itself: users and services alike.
+                "require": ["exp", "sub"],
```

```python
def _token_kind(claims: dict[str, Any]) -> str:
    """Return ``user`` or ``service`` from Module 3's ``kind`` claim.

    Tokens issued before that claim existed carry only ``role``: user tokens
    are recognized by ``role: user`` and everything else is a service.
    """

    kind = claims.get("kind")
    if kind in ("user", "service"):
        return kind
    return "user" if claims.get("role") == "user" else "service"
```

### 2. Proxy al Módulo 3

Mismo patrón que `POST /api/auth/token`, con el mismo camino a los dos lados.
No hay que reescribir el `Path` de la cookie. Se agregan cinco funciones en
`gateway/app/api/routes/module3.py`, después de `issue_module3_token`:

```python
@router.post("/auth/register")
async def register_user(
    request: Request,
    upstream_service: UpstreamService = Depends(get_upstream_service),
) -> Response:
    """Forward a user registration to Module 3."""

    return await proxy_request(
        request, upstream_service, UpstreamModule.MODULE3, "/api/auth/register"
    )


@router.post("/auth/login")
async def login_user(
    request: Request,
    upstream_service: UpstreamService = Depends(get_upstream_service),
) -> Response:
    """Forward a login to Module 3, keeping its refresh cookie."""

    return await proxy_request(
        request, upstream_service, UpstreamModule.MODULE3, "/api/auth/login"
    )


@router.get("/auth/me")
async def current_user(
    request: Request,
    upstream_service: UpstreamService = Depends(get_upstream_service),
) -> Response:
    """Forward the authenticated user's account lookup to Module 3."""

    return await proxy_request(
        request, upstream_service, UpstreamModule.MODULE3, "/api/auth/me"
    )


@router.post("/auth/refresh")
async def refresh_session(
    request: Request,
    upstream_service: UpstreamService = Depends(get_upstream_service),
) -> Response:
    """Forward the refresh cookie to Module 3 and return the rotated one."""

    return await proxy_request(
        request, upstream_service, UpstreamModule.MODULE3, "/api/auth/refresh"
    )


@router.post("/auth/logout")
async def logout_user(
    request: Request,
    upstream_service: UpstreamService = Depends(get_upstream_service),
) -> Response:
    """Forward a logout to Module 3 so it revokes the session cookie."""

    return await proxy_request(
        request, upstream_service, UpstreamModule.MODULE3, "/api/auth/logout"
    )
```

### 3. Cookies y cabeceras

No hay que cambiar nada. `build_forward_headers` reenvía `Cookie`,
`Origin` y `X-CSRF-Protection`, y `_response_from_upstream` conserva cada
`Set-Cookie`. Los tests del parche lo cubren:
`test_module3_session_routes_are_public_and_forward_cookies` y
`test_module3_refresh_returns_the_rotated_cookie`. NGINX tampoco toca esas
cabeceras con la configuración actual.

### 4. HTTPS, CORS y cookies: hay que acordarlo

La VPS hoy expone NGINX solo en el puerto 80 y no sirve la UI. Lo
recomendado es HTTPS y la UI bajo el mismo dominio que el gateway (NGINX con
`/` para la UI y `/api` para el gateway). Así no hace falta CORS y la cookie
`Strict` funciona tal cual.

Si la UI queda en otro sitio, hacen falta cuatro cosas:

- **En el gateway:** `CORSMiddleware` con el origen exacto de la UI,
  `allow_credentials=True` y `X-CSRF-Protection` en `allow_headers`. Con
  credenciales, `allow_origins=["*"]` no sirve.
- **En el Módulo 3:** `AUTH_COOKIE_SAMESITE=none`, HTTPS (`Secure`) y
  `AUTH_ALLOWED_ORIGINS` con el mismo origen de la UI.
- **En la UI:** `credentials: 'include'` en `refresh` y `logout`.
- **Mientras no haya HTTPS:** `AUTH_COOKIE_SECURE=false`. El navegador
  descarta una cookie `Secure` que llega por HTTP desde un host que no es
  `localhost`.

### 5. Rate limiting (recomendado)

El límite global por IP (60 por minuto) ya cubre estas rutas. Para `login` y
`register` conviene uno más bajo, por ejemplo 10 por minuto: en el módulo no
hay bloqueo por intentos fallidos.

## Qué necesita el frontend

- Guardar el `access_token` **en memoria**, no en `localStorage`. Mandarlo
  como `Authorization: Bearer`, igual que hoy.
- Al cargar la app, y cuando una llamada responda 401, hacer
  `POST /api/auth/refresh` con la cabecera `X-CSRF-Protection: 1`. Si
  responde 200, hay sesión; si responde 401, mandar al login. `logout` lleva
  la misma cabecera.
- Hacer **un solo refresh a la vez**: compartir la promesa entre pestañas o
  llamadas. Dos refresh simultáneos con la misma cookie se toman como un
  token robado, y se cierra la sesión.
- `remember` es el checkbox "Recordar sesión" del login.
- El registro puede mandar el `RegisterPayload` actual
  (`username`, `email`, `password`); `confirm` y `terms` se validan en la
  UI. Los errores llegan con el sobre del proyecto,
  `{ status: "error", code, message }`, y el `message` se puede mostrar tal
  cual.

| Situación | HTTP | `code` |
|---|---|---|
| Correo ya registrado | 409 | `DUPLICATE_CONTENT` |
| Campo inválido (correo, nombre de 1 a 80 caracteres, contraseña de 8 a 128) | 422 | `INVALID_METADATA` |
| Correo o contraseña incorrectos | 401 | `UNAUTHORIZED` |
| Cookie ausente, vencida, revocada o reutilizada | 401 | `UNAUTHORIZED` |
| `refresh` o `logout` sin `X-CSRF-Protection`, o desde un origen no permitido | 403 | `UNAUTHORIZED` |
| Publicación de otro usuario | 404 | `CONTENT_NOT_FOUND` |

## Criterios de aceptación de extremo a extremo

Con el gateway en `$GW`, cada paso debe dar el resultado indicado:

```bash
GW=http://localhost:8000
J=(-H "Content-Type: application/json")
C=(-H "X-CSRF-Protection: 1")

# 1. Registro → 201
curl -i "${J[@]}" -d '{"displayName":"Ana","email":"ana@x.cl","password":"clave-segura-1"}' $GW/api/auth/register
# 2. El mismo correo otra vez → 409 DUPLICATE_CONTENT
curl -i "${J[@]}" -d '{"username":"otra","email":" ANA@x.cl","password":"clave-segura-1"}' $GW/api/auth/register
# 3. Contraseña incorrecta → 401 y sin Set-Cookie
curl -i "${J[@]}" -d '{"email":"ana@x.cl","password":"mal"}' $GW/api/auth/login
# 4. Login → 200 con access_token y Set-Cookie: pubtube_refresh=...; HttpOnly; Path=/api/auth
ANA=$(curl -s -c ana.jar "${J[@]}" -d '{"email":"ana@x.cl","password":"clave-segura-1"}' $GW/api/auth/login | jq -r .access_token)
# 5. /me sin token → 401; con token → 200 con displayName "Ana"
curl -i $GW/api/auth/me
curl -s -H "Authorization: Bearer $ANA" $GW/api/auth/me
# 6. Ana programa → 202; Ana consulta → 200; otro usuario consulta → 404
ID=$(curl -s -H "Authorization: Bearer $ANA" "${J[@]}" \
  -d "{\"contentId\":\"c-1\",\"scheduleAt\":\"$(date -u -d '+1 hour' +%FT%TZ)\",\"timezone\":\"UTC\"}" \
  $GW/api/publish/schedule | jq -r .publishId)
curl -i -H "Authorization: Bearer $ANA" $GW/api/publish/$ID/status
# 7. Refresh sin la cabecera CSRF → 403; con ella → 200, JWT nuevo y cookie distinta
curl -i -b ana.jar -X POST $GW/api/auth/refresh
cp ana.jar ana.antes; curl -s -b ana.jar -c ana.jar "${C[@]}" -X POST $GW/api/auth/refresh
# 8. Reusar la cookie anterior → 401, y la nueva también queda revocada
curl -i -b ana.antes "${C[@]}" -X POST $GW/api/auth/refresh
# 9. El paso 8 cerró la sesión: nuevo login. Logout → 204 con la cookie
#    borrada; refresh con la cookie de antes del logout → 401
curl -s -c ana.jar "${J[@]}" -d '{"email":"ana@x.cl","password":"clave-segura-1"}' $GW/api/auth/login >/dev/null
cp ana.jar ana.antes; curl -i -b ana.jar -c ana.jar "${C[@]}" -X POST $GW/api/auth/logout
curl -i -b ana.antes "${C[@]}" -X POST $GW/api/auth/refresh
# 10. Servicios: /api/auth/token sigue igual → 200 con kind "service"
curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -d grant_type=client_credentials $GW/api/auth/token
```

El CI del Módulo 3 corre en cada push los pasos 1, 4, 5, 6, 7 y 9, con la
imagen `pubtube-mod4:develop`. Hasta que el gateway publique estas rutas,
la cuenta y la sesión van directo al módulo, y el JWT de usuario pasa por el
gateway. Cuando lo integren, basta con apuntar `m3` a `$gw` en ese paso del
workflow. El resultado de la verificación completa está en
[`pruebas-issue-11.md`](pruebas-issue-11.md).
