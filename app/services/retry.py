"""US-C5 · Política de reintentos (ADR-0006).

La clase del error (transitorio / diferible / definitivo) viene de
`app.domain.errors.classify`; acá se traduce en dos preguntas:

  should_retry()        -> ¿quedan intentos o fallamos ya?
  retry_delay_seconds() -> ¿cuánto esperar hasta el próximo intento?

Separar la política del handler permite probarla sin tocar la base de datos
ni al scheduler, y que `services/recovery.py` calcule exactamente el mismo
delay cuando tiene que reprogramar un job que se perdió.
"""

from __future__ import annotations

from app.config import get_settings
from app.domain.errors import ErrorClass


def should_retry(error_class: ErrorClass, attempts: int) -> bool:
    """¿Conviene reintentar? `attempts` = intentos YA realizados.

    Un error definitivo nunca se reintenta, sin importar cuántos intentos
    queden: reintentarlo solo demora el publish.failed y gasta cuota.
    """
    if error_class is ErrorClass.DEFINITIVO:
        return False
    return attempts < get_settings().PUBLISH_MAX_ATTEMPTS


def retry_delay_seconds(error_class: ErrorClass, attempts: int) -> int:
    """Segundos desde el fallo actual hasta el próximo intento.

    `attempts` = intentos ya realizados (1 = falló el primero).

    - TRANSITORIO: backoff exponencial sobre PUBLISH_RETRY_BACKOFF_SECONDS.
      Con 60: 60 s, 120 s, 240 s... Cada fallo suele ser más probable de
      resolver esperando un poco más, no un poco menos.
    - DIFERIBLE (cuota): delay fijo PUBLISH_QUOTA_RETRY_SECONDS. La cuota se
      restablece por ventana; acortarlo solo consume cuota en llamadas que
      van a fallar igual.
    """
    settings = get_settings()
    if error_class is ErrorClass.DIFERIBLE:
        return settings.PUBLISH_QUOTA_RETRY_SECONDS
    # max(..., 0): si alguna rama llamara con attempts = 0, el delay es la
    # base y no un fraccionario.
    return settings.PUBLISH_RETRY_BACKOFF_SECONDS * 2 ** max(attempts - 1, 0)
