"""Proceso auxiliar para las pruebas de reinicio de US-C2 (no es un test).

Se lanza con `python -m tests.proceso_scheduler <modo> <segundos>` sobre la
misma DATABASE_URL que la prueba, programa una publicación con el scheduler
real y termina con os._exit, sin apagado ordenado, como un contenedor que se
cae. Imprime el publish_id en stdout.

Modos:
  programar  -> registra el job y muere antes de que se dispare.
  colgar     -> espera a que el job tome la publicación (estado 'publishing')
                y muere a mitad de la publicación.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone


def main() -> None:
    modo, segundos = sys.argv[1], float(sys.argv[2])

    from app.infra.db import SessionLocal
    from app.infra.models import Publication
    from app.scheduler.scheduler import start_scheduler
    from app.services.scheduling import schedule_publication

    start_scheduler()
    session = SessionLocal()
    pub = schedule_publication(
        session,
        content_id=f"c-proceso-{modo}",
        schedule_at=datetime.now(timezone.utc) + timedelta(seconds=segundos),
        tz_name="America/Santiago",
    )

    if modo == "colgar":
        limite = time.monotonic() + 15
        while time.monotonic() < limite:
            session.expire_all()
            if session.get(Publication, pub.id).state == "publishing":
                break
            time.sleep(0.05)
        else:
            print("TIMEOUT", flush=True)
            os._exit(2)

    print(pub.id, flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
