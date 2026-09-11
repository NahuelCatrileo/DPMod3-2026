"""Configuración del módulo. Todo llega por variables de entorno (12-factor).

Nada de valores reales aquí: los defaults son de desarrollo local y
deben poder sobrescribirse desde .env / docker-compose.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Identidad del módulo -------------------------------------------
    MODULE_NAME: str = "module-3"
    APP_ENV: Literal["dev", "test", "prod"] = "dev"
    LOG_LEVEL: str = "INFO"

    # --- Base de datos ---------------------------------------------------
    DATABASE_URL: str = "postgresql+psycopg2://pubtube:pubtube@postgres:5432/pubtube_m3"

    # --- Publicador (US-C4) ----------------------------------------------
    # mock    -> MockPublisher, no toca la red
    # youtube -> YouTubePublisher, requiere OAuth (Sprint 3)
    PUBLISHER_MODE: Literal["mock", "youtube"] = "mock"
    MOCK_LATENCY_SECONDS: float = 0.5
    # 0.0 = el mock nunca falla. 0.2 = ~20% de los contentId fallan,
    # de forma DETERMINISTA (mismo contentId -> mismo resultado siempre).
    MOCK_FAILURE_RATE: float = 0.0

    # --- Transporte de eventos -------------------------------------------
    # log  -> doble de prueba: escribe el envelope a stdout y a memoria
    # amqp -> RabbitMQ real (Equipo B)
    EVENT_TRANSPORT: Literal["log", "amqp"] = "log"
    AMQP_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    AMQP_EXCHANGE: str = "pubtube.events"

    # --- Scheduler (US-C2) ------------------------------------------------
    SCHEDULER_ENABLED: bool = True
    # Si el contenedor estuvo caído cuando tocaba disparar, APScheduler
    # ejecuta igual si el atraso es menor a este margen (en segundos).
    SCHEDULER_MISFIRE_GRACE_SECONDS: int = 3600
    SCHEDULER_TIMEZONE: str = "UTC"

    # --- OAuth / YouTube (Sprint 3, US-C3) --------------------------------
    GOOGLE_CLIENT_SECRETS_FILE: str = "credentials/client_secret.json"
    GOOGLE_TOKEN_FILE: str = "credentials/token.json"
    OAUTH_REDIRECT_URI: str = "http://localhost:8000/oauth2/callback"
    SESSION_SECRET_KEY: str = "cambiame-en-.env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
