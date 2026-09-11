#!/usr/bin/env python3
"""Genera la evidencia de contrato del Módulo 3 para la reunión de integración.

Ejecuta el flujo completo contra el doble de prueba y escribe los tres
envelopes reales que este módulo emite, tal como saldrían al broker.

    python scripts/evidencia_contrato.py

Salida: docs/contratos/evidencia-eventos.json  (para el acta y el repo)
        y un resumen legible por consola (para la reunión).

No requiere Docker, ni Postgres, ni RabbitMQ: usa SQLite temporal y el
LogEventPublisher. Corre en menos de dos segundos.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_db = Path(tempfile.mkdtemp()) / "evidencia.db"
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{_db}",
        "PUBLISHER_MODE": "mock",
        "EVENT_TRANSPORT": "log",
        "MOCK_LATENCY_SECONDS": "0",
        "MOCK_FAILURE_RATE": "0",
        "SCHEDULER_ENABLED": "false",
        "SESSION_SECRET_KEY": "evidencia",
        "LOG_LEVEL": "ERROR",
    }
)

from datetime import datetime, timedelta, timezone  # noqa: E402

import app.infra.models  # noqa: F401,E402
from app.infra.db import Base, SessionLocal, engine  # noqa: E402
from app.infra.events.publisher import (  # noqa: E402
    LogEventPublisher,
    set_event_publisher,
)
from app.infra.publishers.factory import set_publisher  # noqa: E402
from app.infra.publishers.mock import MockPublisher  # noqa: E402
from app.services.publishing import execute_publication  # noqa: E402
from app.services.scheduling import schedule_publication  # noqa: E402

Base.metadata.create_all(bind=engine)
doble = LogEventPublisher()
set_event_publisher(doble)

CORRELATION_ID = "corr-evidencia-2026-09-10"


def _flujo(content_id: str, falla: bool) -> None:
    set_publisher(MockPublisher(latency_seconds=0, failure_rate=1.0 if falla else 0.0))
    session = SessionLocal()
    try:
        pub = schedule_publication(
            session,
            content_id=content_id,
            schedule_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            tz_name="America/Santiago",
            correlation_id=CORRELATION_ID if not falla else f"{CORRELATION_ID}-fail",
        )
    finally:
        session.close()
    execute_publication(pub.id)


_flujo("c-demo-ok", falla=False)
time.sleep(0.05)
_flujo("c-demo-error", falla=True)

# Un envelope de ejemplo por cada tipo del catálogo.
ejemplos: dict[str, dict] = {}
for ev in doble.published:
    ejemplos.setdefault(ev["type"], ev)

salida = {
    "modulo": "module-3",
    "equipo": "C",
    "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "envelopeVersion": 1,
    "publicamos": ["publish.scheduled", "publish.completed", "publish.failed"],
    "consumimos": ["metadata.updated (pendiente de acuerdo con Equipo A)"],
    "puntosAbiertos": [
        "Campo 'source': incluido segun Doc 3 §3.1 y la columna source de "
        "event_store (Doc 3 §6). El Doc 1 §6.3 lo omite. ¿Equipo B lo acepta?",
        "Payload exacto de metadata.updated y fecha de disponibilidad. ¿Equipo A?",
    ],
    "ejemplos": ejemplos,
}

destino = RAIZ / "docs" / "contratos" / "evidencia-eventos.json"
destino.parent.mkdir(parents=True, exist_ok=True)
destino.write_text(json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")

print("=" * 68)
print("  EVIDENCIA DE CONTRATO — Modulo 3 (Equipo C)")
print("=" * 68)
for tipo, envelope in ejemplos.items():
    print(f"\n--- {tipo} ---")
    print(json.dumps(envelope, indent=2, ensure_ascii=False))

print("\n" + "=" * 68)
print("PREGUNTAS PARA LA REUNION")
for i, p in enumerate(salida["puntosAbiertos"], 1):
    print(f"  {i}. {p}")
print(f"\nArchivo generado: {destino.relative_to(RAIZ)}")
print("=" * 68)
