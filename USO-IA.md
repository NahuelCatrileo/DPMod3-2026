# Declaración de Uso de IA Generativa — Equipo C (Módulo 3)

Guía del Estudiante §8.1 y Anexo D. Se actualiza en cada sprint.
El uso de IA está permitido; lo que se sanciona es no declararlo.

| Sprint | Herramienta | En qué se usó | Verificación humana aplicada | Responsable |
|---|---|---|---|---|
| S1 | Claude | Borrador de la estructura por capas y del código de US-C4, US-C1 y el esqueleto de US-C2 | *(completar: quién ejecutó los tests, quién revisó el PR)* | *(completar)* |
| S1 | Claude | Redacción de los borradores de ADR-0002 y ADR-0003 | *(completar: la decisión fue tomada por el equipo en la sesión del ..., las alternativas se discutieron)* | *(completar)* |
| S1 | Claude | Revisión crítica del repositorio y del desglose de subtareas | *(completar: qué observaciones se aceptaron y cuáles se descartaron)* | *(completar)* |
| S2 | Claude | US-C2: diagnóstico del scheduler contra los criterios de aceptación y borrador del código de reconciliación tras reinicio y de cancelación (TECH-C2.4) | *(completar)* | *(completar)* |
| S2 | Claude | US-C2: borrador de las pruebas de disparo, cancelación y reinicio (TECH-C2.5 y TECH-C2.6). La herramienta ejecutó la suite y comprobó que las pruebas fallan si se desactiva la reconciliación o la cancelación | *(completar)* | *(completar)* |
| S2 | Claude | Redacción del borrador de ADR-0004 (estado Propuesta) | *(completar: quién revisó la decisión de marcar fallidas las publicaciones vencidas)* | *(completar)* |
| S2 | Claude | Borrador de la verificación JWT de la API (`app/api/auth.py`), de sus pruebas (`tests/test_auth_jwt.py`) y del script `scripts/generar_jwt.py`. La herramienta ejecutó la suite completa | *(completar: quién revisó el código y acordó la clave y los claims con Equipo D)* | *(completar)* |
| S2 | Claude | Redacción del borrador de ADR-0005 (estado Propuesta) | *(completar: quién revisó la decisión de verificar el JWT también en el módulo)* | *(completar)* |
| S2 | Claude | US-C5: clasificación de errores (transitorio / diferible / definitivo) en `app/domain/errors.py`, política de reintentos en `app/services/retry.py` y su aplicación en el handler y en la reconciliación, más el borrador de ADR-0006. La herramienta ejecutó la suite completa con cobertura | *(completar: quién revisó el mapeo de cada código del catálogo y quién corrió la demo de reintentos)* | *(completar)* |
| S2 | Claude | Emisión de JWT a clientes de servicio: `POST /api/auth/token` (`app/api/routes_auth.py`), hash y verificación de secretos (`app/services/client_credentials.py`), sus pruebas (`tests/test_auth_token.py`), el script `scripts/generar_cliente.py` y el borrador de ADR-0007 (estado Propuesta). La herramienta ejecutó la suite completa con cobertura | *(completar: quién revisó el código y confirmó con Equipo D quién emite los tokens)* | *(completar)* |
| S2 | Claude | Revisión de la emisión de tokens contra el código del gateway de Equipo D (imagen `pubtube-mod4:develop`) y prueba local módulo → gateway → módulo con Docker; corrección de la guarda de producción de `JWT_SECRET_KEY`; paso de CI que usa un token emitido por el módulo a través del gateway; ADR-0007 pasa a Aceptada y se actualizan ADR-0005, el contrato REST, el README y los comentarios que decían que el gateway emite los tokens. La herramienta ejecutó la suite completa y la prueba de integración | *(completar: quién revisó los hallazgos sobre el gateway y los llevó a Equipo D)* | *(completar)* |
| S3 | Claude | US-C3.1–C3.3: revisión del código OAuth del Sprint 0, donde encontró que el flujo nunca terminaba (sin PKCE) y que el callback no validaba `state`. Borrador de `app/oauth/` (cliente de Google, consentimiento con PKCE y `state` en el servidor, refresh con lock, tokens cifrados con Fernet en `oauth_credential`), la migración `0002`, el rol admin en `/oauth2/*` y las pruebas `tests/test_oauth_*.py`. La herramienta ejecutó la suite completa y comprobó con mutaciones que las pruebas fallan si se quita PKCE, la validación de `state`, el cifrado, el rol admin o el lock. También probó contra Google real, con credenciales inventadas, la forma de los errores que se traducen | *
| S3 | Claude | Redacción del borrador de ADR-0008 (estado Propuesta) y de la guía `docs/oauth-youtube.md` | *
| S3 | Claude | Issue #11 (pedido del Módulo 4): diagnóstico de lo que existía contra el issue, el gateway (`PubTube-Mod4`, `develop`) y las vistas de login del frontend (`PubTube-ui`, `feature/auth-views`). Borrador de las cuentas y sesiones: tablas `app_user` y `refresh_token` y `publication.owner_id` (migración `0003`), Argon2id, refresh token rotado en cookie HttpOnly con detección de reuso, rutas `/api/auth/{register,login,me,refresh,logout}`, firma común en `app/services/tokens.py`, permisos por dueño en `/api/publish/*`, pruebas `tests/test_user_auth.py` y `tests/test_publish_ownership.py`, y paso de CI de extremo a extremo con el gateway. La herramienta ejecutó la suite completa y comprobó con mutaciones que las pruebas fallan si se quita el control de dueño, la detección de reuso, la revocación del logout, el vencimiento o el hash. Probó la migración contra PostgreSQL 16 (upgrade y downgrade) y el flujo completo contra la imagen del gateway, original y parcheada | *(completar: quién revisó el código y las decisiones de ADR-0009, y quién le llevó el parche del gateway al Equipo D)* | *(completar)* |
| S3 | Claude | Borrador de ADR-0009 (estado Propuesta) y de `docs/gateway-auth-usuarios.md`, con el parche del gateway probado contra su suite | *(completar)* | *(completar)* |

> **Antes de entregar esto, léanlo de nuevo.** Las columnas vacías son suyas
> y no las puede llenar la herramienta. La regla práctica del Review es que si
> el docente pregunta "¿por qué eligieron este patrón?" y nadie responde sin
> leer, la herramienta tomó una decisión que debía tomar el equipo. Eso no se
> sanciona como falta, pero se evalúa como ausencia de dominio técnico, que
> pesa 25 % en la defensa final.
>
> Concretamente, asegúrense de que al menos una persona pueda explicar sin
> apoyo: por qué el mock no emite eventos, por qué el fallo del mock es
> determinista, y por qué la transición `pending → publishing` se hace con un
> UPDATE condicional en vez de leer-y-escribir.

> **Nota S2 (US-C2).** ADR-0004 queda en estado Propuesta: la decisión de
> marcar fallida una publicación vencida, en vez de publicarla tarde, la debe
> tomar y poder defender el equipo. Al menos una persona debe poder explicar
> sin apoyo por qué el scheduler arranca en pausa y por qué la reconciliación
> solo toca las filas en `publishing` al arrancar.

> **Nota S3 (US-C3.1–C3.3).** ADR-0008 queda en estado Propuesta. Al menos
> una persona debe poder explicar sin apoyo: para qué sirven el `state` y
> PKCE, y por qué el `state` se guarda en el servidor y no en la cookie; por
> qué los tokens van cifrados en la base y no en `token.json`; por qué
> `invalid_grant` borra la credencial y un corte de red no; y por qué la
> cuenta hay que reconectarla antes de la demo.

Declaramos que la información anterior es completa y veraz, y que todo
incremento presentado cumple la Definition of Done y puede ser explicado por
el equipo.
