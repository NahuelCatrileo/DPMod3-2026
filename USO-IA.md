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

Declaramos que la información anterior es completa y veraz, y que todo
incremento presentado cumple la Definition of Done y puede ser explicado por
el equipo.
