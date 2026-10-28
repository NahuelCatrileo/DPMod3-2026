# Informe de pruebas — issue #11 (cuentas de usuario y sesiones)

2026-10-28 · Módulo 3 (Equipo C) · ADR-0009

## Resumen

El Módulo 3 cumple los criterios de aceptación del issue #11 que dependen de
él, verificados sobre PostgreSQL 16 con `APP_ENV=prod`. El flujo obligatorio
de la sección 10 funciona **a través del gateway con el parche** de
[`gateway-auth-usuarios.md`](gateway-auth-usuarios.md): 74 chequeos pasan y
ninguno falla.

**El issue no se puede cerrar todavía.** El gateway `develop` del Módulo 4,
sin el parche, responde 401 a `register` y `login`. Esa es la única
dependencia que bloquea el flujo completo. Hay además tres pendientes:

- acordar HTTPS, CORS y cookies;
- desplegar en la VPS;
- conectar el frontend.

## Versiones probadas

| Componente | Versión |
|---|---|
| Módulo 3 anterior | `dev` en `8bd50b2` (migración `0003`) |
| Módulo 3 nuevo | rama `feat/ISSUE-11-modelo-usuario-csrf` (migración `0004`) |
| Gateway parcheado | `PubTube-Mod4` `develop` en `7b8b2b3` + `scripts/verificacion_issue11/gateway-issue-11.patch` |
| Gateway oficial | imagen `ghcr.io/tilininsano312/pubtube-mod4:develop` |
| Base de datos | `postgres:16-alpine` |

## Pruebas automatizadas

| Suite | Resultado |
|---|---|
| Módulo 3: `pytest`, más `ruff check app tests` | 407 pasan, 1 omitida · cobertura 95,8 % (umbral de la DoD: 70 %) |
| Pruebas del issue #11 en el Módulo 3 | 93: `test_user_auth.py`, `test_publish_ownership.py`, `test_migraciones.py`, `test_auth_token.py`, `test_auth_jwt.py` |
| Gateway con el parche: `pytest`, más `ruff --select E4,E7,E9,F` | 108 pasan (103 que ya existían y 5 nuevos) |
| Mutaciones (¿los tests fallan si se quita la protección?) | 14 de 14 detectadas |

Las mutaciones quitaron, una por vez:
- el control de dueño;
- la detección de reuso del refresh;
- la revocación en el logout;
- el vencimiento del refresh;
- la restricción de `/me` a usuarios;
- el hash de la contraseña;
- la cabecera CSRF;
- la revisión de `Origin`;
- el claim `kind`;
- el rechazo de clientes con `role: user`;
- la excepción de admin;
- la conversión de UUID en la migración de SQLite;
- el alias `username`;
- el rol de la cuenta en el JWT.

En todos los casos la suite falló.

## Prueba de extremo a extremo

`bash scripts/verificacion_issue11/run.sh` simula la actualización que va a
hacer la VPS:

1. PostgreSQL con publicaciones y una credencial OAuth cargadas en la
   migración `0002`.
2. El Módulo 3 anterior (`0003`) registra dos cuentas, abre una sesión y
   programa una publicación, a través del gateway.
3. El Módulo 3 nuevo arranca sobre la misma base y migra a `0004`.
4. Todo lo de abajo se comprueba a través del gateway, con
   `MODULE3_URL=http://module3-api:8000`.

Para reproducir las condiciones de la VPS, el módulo corre con `APP_ENV=prod`
y `AUTH_COOKIE_SECURE=false`, porque la prueba va por HTTP, igual que la VPS
hoy. También corre con `AUTH_ALLOWED_ORIGINS=https://pubtube.example`. La
clave JWT y el cliente de servicio son efímeros: no son credenciales reales.

| Grupo | Chequeos | Resultado |
|---|---|---|
| Sección 10: flujo obligatorio a través del gateway | 17 | PASS |
| Migración 0003 → 0004 sobre la base existente (C14) | 14 | PASS |
| OpenAPI (C18) | 7 | PASS |
| Casos de error: registro, login, JWT vencido o alterado, `sub` faltante, reuso de refresh | 15 | PASS |
| CSRF | 5 | PASS |
| Servicios: `/api/auth/token`, `kind`, acceso a todas las publicaciones | 8 | PASS |
| Rol de la cuenta en el JWT | 2 | PASS |
| PostgreSQL, Argon2id y hash del refresh | 6 | PASS |
| **Total** | **74** | **74 PASS, 0 FAIL** |
| Gateway `develop` oficial, sin el parche (dependencia del M4) | 3 | `register` 401, `login` 401; el JWT de usuario en `/api/publish/*` sí pasa (200) |

<details>
<summary>Salida completa de la corrida</summary>

```text
== C14 · Migración 0003 → 0004 sobre la base existente
PASS | C14 | versión de alembic: 0004
PASS | C14 | columnas de app_user (sección 2): id:uuid:NO,email_normalized:character varying(255):NO,display_name:character varying(80):NO,password_hash:text:NO,is_active:boolean:NO,created_at:timestamp with time zone:NO,updated_at:timestamp with time zone:NO,role:character varying(30):NO
PASS | C14 | role por defecto 'user': 'user'::character varying
PASS | C14 | is_active por defecto true: true
PASS | C14 | única restricción UNIQUE: email_normalized: app_user_email_normalized_key
PASS | C14 | refresh_token.user_id es UUID con FK: uuid refresh_token_user_id_fkey
PASS | C14 | publicaciones previas a 0003 intactas y sin dueño: 2
PASS | C14 | credencial OAuth intacta: 1
PASS | C14 | cuentas de la versión anterior conservadas: vieja@example.com|vieja|user
PASS | C14 | la cuenta previa entra con su contraseña: 200
PASS | C14 | y conserva su id: 8266a2fe-a34f-4aab-9bf8-2fc362e59d4d
PASS | C14 | la sesión abierta antes de migrar sigue sirviendo: 200
PASS | C14 | el token previo (sin kind) ve su publicación: 200
PASS | C14 | el token previo de otro usuario no la ve: 404
== C18 · OpenAPI del módulo
PASS | C18 | OpenAPI documenta /api/auth/register: true
PASS | C18 | OpenAPI documenta /api/auth/login: true
PASS | C18 | OpenAPI documenta /api/auth/me: true
PASS | C18 | OpenAPI documenta /api/auth/refresh: true
PASS | C18 | OpenAPI documenta /api/auth/logout: true
PASS | C18 | OpenAPI documenta /api/auth/token: true
PASS | C18 | OpenAPI: registro con displayName: true
== S10 · Flujo obligatorio a través del gateway
PASS | S10 | POST /api/auth/register: 201
PASS | S10 | register guarda display_name y email normalizado: Ana Pérez ana@example.com user
PASS | S10 | register acepta username como alias: 201 beto
PASS | S10 | POST /api/auth/login: 200
PASS | S10 | login deja la refresh cookie (HttpOnly, Path=/api/auth): si
PASS | S10 | JWT: sub, role y kind: ca7ea409-547b-4a6f-a090-fadbcc838a05 user user
PASS | S10 | GET /api/auth/me: 200
PASS | S10 | /me identifica a ana: ca7ea409-547b-4a6f-a090-fadbcc838a05 Ana Pérez
PASS | S10 | POST /api/publish/schedule: 202
PASS | S10 | propietario asociado en PostgreSQL: ca7ea409-547b-4a6f-a090-fadbcc838a05
PASS | S10 | GET status: 200 para el propietario: 200
PASS | S10 | GET status: rechazo para otro usuario: 404
PASS | S10 | POST /api/auth/refresh: 200
PASS | S10 | refresh entrega un JWT nuevo: true
PASS | S10 | refresh rota la cookie: distinta
PASS | S10 | POST /api/auth/logout: 204
PASS | S10 | POST /api/auth/refresh tras logout: 401
== Casos de error
PASS | C04 | correo duplicado (espacios y mayúsculas): 409 DUPLICATE_CONTENT
PASS | ERR | display_name repetido sí se permite: 201
PASS | ERR | registro inválido (contraseña corta): 422 INVALID_METADATA
PASS | ERR | registro con display_name de 81 caracteres: 422
PASS | ERR | el registro no deja elegir rol: 201 user
PASS | ERR | contraseña incorrecta: 401 UNAUTHORIZED
PASS | ERR | login fallido no deja cookie: no
PASS | ERR | correo inexistente: 401 UNAUTHORIZED
PASS | C07 | /me sin token: 401
PASS | C07 | JWT alterado (role→admin) en el gateway: 401
PASS | C07 | JWT alterado directo al módulo: 401
PASS | C07 | JWT vencido: 401
PASS | GW | JWT sin sub: lo rechaza el gateway: gateway
PASS | C10 | reusar un refresh ya rotado: 401
PASS | C10 | el reuso revoca también el refresh vigente: 401
== CSRF (fase 2 del issue)
PASS | CSRF | refresh sin X-CSRF-Protection: 403 UNAUTHORIZED
PASS | CSRF | logout sin X-CSRF-Protection: 403
PASS | CSRF | Origin no permitido: 403
PASS | CSRF | la sesión sobrevive a los intentos rechazados (Origin de la UI): 200
PASS | CSRF | login solo acepta JSON (text/plain): 422
== C09 / C13 · Servicios (client credentials) y rol de la cuenta
PASS | C13 | /api/auth/token por el gateway oficial: 200
PASS | C13 | /api/auth/token por el gateway parcheado: 200
PASS | C09 | token de servicio: role y kind: service service
PASS | C13 | secreto de cliente incorrecto: 401
PASS | C13 | el servicio programa: 202
PASS | C13 | el servicio ve la publicación de ana: 200
PASS | C13 | el servicio ve una publicación previa a 0003: 200
PASS | C12 | un usuario no ve publicaciones sin dueño: 404
PASS | C09 | token de servicio en /me: 403 UNAUTHORIZED
PASS | ROL | role de la columna va al JWT: admin user
PASS | ROL | un usuario admin ve publicaciones ajenas: 200
== C02 / C03 · PostgreSQL y Argon2id
PASS | C02 | cuentas en app_user: 6
PASS | C03 | hashes que no son $argon2id$: 0
PASS | C03 | contraseñas en claro: 0
PASS | SEG | el valor de la cookie no está en la base: 0
PASS | SEG | sí está su SHA-256: 1
== Gateway develop sin el parche (dependencia del Módulo 4)
PEND | C15 | register por el gateway oficial: 401
PEND | C15 | login por el gateway oficial: 401
PEND | C15 | JWT de usuario por el gateway oficial (publish/status del dueño): 200
RESULTADO M3 + gateway parcheado: 74 PASS, 0 FAIL
```

</details>

### Downgrade de `0004` en PostgreSQL

Probado a mano sobre la base de la corrida anterior:

- **Con dos cuentas con el mismo `display_name`,** el downgrade falla al
  recrear `username` único. La base queda en `0004` sin cambios, porque
  PostgreSQL revierte todo el DDL de la migración.
- **Sin nombres repetidos,** vuelve a `0003`. Las 5 cuentas y las 12 sesiones
  conservan su FK.
- **Un `upgrade` posterior** vuelve a `0004` sin errores.

## Criterios de aceptación (sección 9 del issue)

| # | Criterio | Estado | Evidencia |
|---|---|---|---|
| 1 | Un usuario puede registrarse | ✅ | S10: `register` 201 |
| 2 | Los usuarios se guardan en PostgreSQL | ✅ | C02 y C14: columnas de la sección 2 en PostgreSQL 16 |
| 3 | Contraseñas como hash Argon2id | ✅ | C03: 0 hashes sin `$argon2id$`, 0 en claro |
| 4 | No se registran correos duplicados | ✅ | C04: 409, aunque cambien espacios y mayúsculas |
| 5 | Un usuario registrado puede iniciar sesión | ✅ | S10: `login` 200 |
| 6 | El login devuelve un JWT válido | ✅ | S10: `sub`, `role` y `kind`; lo aceptan el gateway y el módulo |
| 7 | JWT vencidos o alterados rechazados | ✅ | C07: vencido, `role` cambiado a `admin` (en el gateway y en el módulo), sin token. GW: sin `sub` |
| 8 | `/me` identifica al usuario | ✅ | S10: id y `displayName` correctos |
| 9 | Los tokens de servicio no se confunden con usuarios | ✅ | C09: `kind: service`, 403 en `/me`; la configuración rechaza clientes con `role: user` |
| 10 | Refresh renueva e invalida el anterior | ✅ | S10 y C10: cookie rotada; el reuso revoca toda la sesión |
| 11 | Logout impide reutilizar el refresh | ✅ | S10: 401 tras el logout |
| 12 | Publicaciones protegidas por propietario | ✅ | S10: 200 para el dueño y 404 para otro. C12, ROL |
| 13 | Los clientes de servicio mantienen su funcionalidad | ✅ | C13: `/api/auth/token` por los dos gateways; ven todas las publicaciones |
| 14 | Migraciones sobre una base existente | ✅ | C14: 0002 → 0003 → 0004 con datos, más el downgrade |
| 15 | El gateway expone las rutas nuevas | ⏳ M4 | Con el parche: S10 completo. `develop` oficial: `register` y `login` responden 401 |
| 16 | Integración con JWT reales | ✅ | Todos los tokens del E2E los emite el módulo; el CI hace lo mismo con la imagen del gateway |
| 17 | Toda la suite termina correctamente | ✅ | 407 tests en M3 y 108 en el gateway |
| 18 | Documentación y OpenAPI al día | ✅ | C18; `docs/contratos/rest.md`, `docs/gateway-auth-usuarios.md`, ADR-0009 |

## Pendiente, fuera del Módulo 3

1. **Gateway (Módulo 4):** integrar el parche (PR separada). Con eso, el
   criterio 15 y la condición de cierre del issue quedan cubiertos. El paso de
   sesión del CI del Módulo 3 pasará entonces a ir entero por el gateway.
2. **HTTPS, CORS y cookies:** la VPS sirve NGINX solo por HTTP en el puerto
   80. Las opciones están en `gateway-auth-usuarios.md`, sección 4.
3. **Despliegue en la VPS:** la migración `0004` corre sola al arrancar el
   módulo, como mostró el C14. No se verificó en la VPS: no hay acceso desde
   el Equipo C.
4. **Frontend:** conectar las vistas de login y registro. El
   `RegisterPayload` actual (`username`) ya funciona.

## Cómo reproducir

```bash
bash scripts/verificacion_issue11/run.sh            # gateway develop 7b8b2b3 + parche
GATEWAY_REF=develop GATEWAY_PATCH= bash scripts/verificacion_issue11/run.sh   # develop del M4 tal cual
```

Requiere Docker y git. Construye las imágenes, levanta todo en una red
propia y la borra al terminar.
