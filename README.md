# PubTube · Módulo 3 — Publicación y Programación

**Equipo C** · Curso Dirección de Proyectos · 2026-2

Consume `metadata.*`, publica `publish.scheduled`, `publish.completed` y
`publish.failed`. Programa publicaciones y las dispara a su hora contra
YouTube Data API v3 (con modo simulado conmutable).

---

## Levantar el módulo

Requisitos: Docker y Docker Compose. Nada más.

```bash
git clone <repo> && cd DPMod3-2026
cp .env.example .env
docker compose up --build
```

Las migraciones corren solas antes de arrancar la API. Verificar:

```bash
curl http://localhost:8000/api/health
```

Documentación interactiva: <http://localhost:8000/docs>

> `.env` **no se versiona**. Genere `SESSION_SECRET_KEY` con
> `openssl rand -hex 32` y cambie `POSTGRES_PASSWORD`.

## Correr los tests

```bash
docker compose --profile tools run --rm tests
```

O en local:

```bash
pip install -r requirements-dev.txt
pytest --cov=app --cov-report=term-missing
ruff check app tests
```

Estado actual: **38 tests, 88 % de cobertura**. El umbral de la DoD es 70 %.

---

## Configuración

| Variable | Valores | Qué hace |
|---|---|---|
| `PUBLISHER_MODE` | `mock` \| `youtube` | US-C4. Conmuta el publicador. `mock` no toca la red ni gasta cuota. |
| `MOCK_FAILURE_RATE` | `0.0`–`1.0` | Fracción de `contentId` que fallan, de forma determinista. `1.0` fuerza el camino de error. |
| `MOCK_LATENCY_SECONDS` | float | Latencia simulada. `0` en tests. |
| `EVENT_TRANSPORT` | `log` \| `amqp` | `log` es el doble de prueba. `amqp` publica a RabbitMQ. |
| `SCHEDULER_ENABLED` | bool | Apagar en tests. |
| `SCHEDULER_MISFIRE_GRACE_SECONDS` | int | Margen para ejecutar jobs atrasados tras un reinicio. |

---

## Estado de las historias

| Historia | SP | Estado | Falta para DoD |
|---|---|---|---|
| US-C4 · Publicador simulado conmutable | 3 | **Completa** | — |
| US-C1 · Programar publicación | 5 | **Completa** | — |
| US-C2 · Scheduler de publicaciones | 8 | **Esqueleto — no comprometida** | Cancelación de jobs, política de reintentos (US-C5), pruebas de integración con reinicio real de contenedor |
| US-C6 · Máquina de estados | 3 | Parcial (adelantada) | Formalizar en Sprint 2 |
| US-C3 · YouTube real | 8 | Esqueleto | Sprint 3 |
| US-C7 · Reintento manual | 3 | No iniciada | Sprint 4 |

**Comprometido en el Sprint 1: 8 SP** (US-C4 + US-C1), igual que el Plan de
Release del Doc 2 §7. El esqueleto del scheduler es tarea técnica, no historia.

---

## Guion de la demo

Ensayar el día anterior, desde clon limpio, en la máquina de alguien que no
desarrolló esa parte (Guía §4.6).

1. **Clon limpio y arranque.** `docker compose up` sin pasos manuales.
   Mostrar `/api/health` con `publisherMode: mock` y `schedulerRunning: true`.

2. **US-C1 · Programar.** `POST /api/publish/schedule` con `scheduleAt` a
   +90 segundos. Mostrar el `202`, el `publishId` y el evento
   `publish.scheduled` en los logs con su `correlationId`.

3. **Validaciones.** Reenviar con fecha pasada → `409 SCHEDULE_CONFLICT`.
   Reenviar con `timezone: "Marte/Olympus_Mons"` → `422 INVALID_METADATA`.
   Reenviar el mismo `contentId` → `409 SCHEDULE_CONFLICT`.

4. **US-C4 + disparo.** Esperar el disparo. Mostrar `GET /status` pasando de
   `pending` a `published` y el evento `publish.completed` con el mismo
   `correlationId` que el paso 2.

5. **Camino de error.** Reiniciar con `MOCK_FAILURE_RATE=1.0`, programar de
   nuevo y mostrar `failed` + `publish.failed` con `errorCode: QUOTA_EXCEEDED`.

6. **Recuperación tras reinicio** (opcional, declarar que US-C2 va como
   esqueleto). Programar a +5 minutos, `docker compose restart module3`,
   y mostrar en `/api/health` que `pendingJobs` sigue en 1.

> Detalle verificado en pruebas: si el job ya venció mientras el contenedor
> estaba caído, APScheduler lo dispara casi de inmediato al volver, así que
> `pendingJobs` marca 0 antes de que alcancen a consultarlo. Para exhibir el
> contador en positivo, usen una ventana de varios minutos.

---

## Puntos abiertos de contrato

Ambos van a la reunión de integración del jueves y deben quedar en acta
(Guía §5.4). No se resuelven por decisión unilateral.

1. **Campo `source` en el envelope.** El Doc 3 §3.1 lo incluye y el
   `event_store` del M2 tiene esa columna; el Doc 1 §6.3 lo omite. Nosotros lo
   enviamos. Si Equipo B valida con `additionalProperties: false`, nuestros
   eventos se van a la DLQ. Confirmar con Equipo B.

2. **Payload de `metadata.updated`.** No está definido qué hacemos al
   recibirlo. Acordar con Equipo A antes del Sprint 2.

---

## Estructura

```
app/
├── config.py              Configuración por entorno (12-factor)
├── main.py                FastAPI + lifespan que arranca el scheduler
├── api/                   Rutas, schemas y sobre de error estándar
├── domain/                Máquina de estados y códigos de error registrados
├── infra/
│   ├── db.py models.py    Persistencia (tabla publication, Doc 3 §6)
│   ├── events/            Envelope + publicador de eventos (doble y AMQP)
│   └── publishers/        Publisher, MockPublisher, YouTubePublisher, factory
├── services/              Casos de uso: programar y ejecutar publicación
├── scheduler/             APScheduler con job store persistente
└── oauth/                 Flujo OAuth 2.0 (preparación de Sprint 3)
```

## Documentación

- `docs/contratos/eventos.md` — envelope, eventos publicados y consumidos
- `docs/contratos/rest.md` — endpoints y mapeo de códigos de error
- `ADR/` — decisiones con alternativas evaluadas
- `USO-IA.md` — declaración de uso de IA generativa (Anexo D)

## Tablero

[Trello — Dirección de Proyecto Mod3](https://trello.com/invite/b/6a8ca74003698bb6600cb7b1/ATTI73e5d16898f38f25964b8446d76718219D28BB2E/direccion-de-proyecto-mod3)
