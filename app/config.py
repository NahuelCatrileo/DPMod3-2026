"""Configuración del módulo. Todo llega por variables de entorno (12-factor).

Nada de valores reales aquí: los defaults son de desarrollo local y
deben poder sobrescribirse desde .env / docker-compose.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.errors import ErrorCode


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
    # Cada cuánto se reconcilia la tabla publication con el job store
    # (ADR-0004). Además corre una vez al arrancar.
    SCHEDULER_RECONCILE_SECONDS: int = 60

    # --- US-C5: política de reintentos y clasificación de errores ----------
    # Intentos totales (el primero + los reintentos) antes de declarar el
    # fallo definitivo y emitir publish.failed.
    PUBLISH_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    # Backoff para errores TRANSITORIOS. Exponencial: con 60 los reintentos
    # caen a los 60 s, 120 s, 240 s... desde el intento anterior.
    PUBLISH_RETRY_BACKOFF_SECONDS: int = Field(default=60, ge=0)
    # Delay para errores DIFERIBLES (cuota). La cuota de la YouTube Data API
    # se restablece por ventana, no por minuto: reintentar cada minuto solo
    # consume cuota en llamadas que van a fallar igual.
    PUBLISH_QUOTA_RETRY_SECONDS: int = Field(default=3600, ge=0)
    # Código con el que MOCK_FAILURE_RATE simula el fallo. Permite ensayar
    # los tres caminos en la demo: OAUTH_ERROR (definitivo, falla ya),
    # PUBLISH_FAILED (transitorio, reintenta con backoff) o QUOTA_EXCEEDED
    # (diferible, reintenta con delay de cuota).
    MOCK_FAILURE_CODE: ErrorCode = ErrorCode.QUOTA_EXCEEDED

    # --- OAuth / YouTube (Sprint 3, US-C3) --------------------------------
    GOOGLE_CLIENT_SECRETS_FILE: str = "credentials/client_secret.json"
    GOOGLE_TOKEN_FILE: str = "credentials/token.json"
    OAUTH_REDIRECT_URI: str = "http://localhost:8000/oauth2/callback"
    SESSION_SECRET_KEY: str = "cambiame-en-.env"

    # --- Autenticación de la API con JWT (ADR-0005) ------------------------
    # Los tokens los emite el API Gateway (Equipo D, US-D1). Este módulo solo
    # los verifica, con la misma clave compartida. El default no sirve fuera
    # de desarrollo: en .env va uno generado con `openssl rand -hex 32`.
    JWT_SECRET_KEY: str = "cambiame-en-.env"
    JWT_ALGORITHM: str = "HS256"
    # Si se completan, el token debe traer exactamente estos `iss` / `aud`.
    JWT_ISSUER: str | None = None
    JWT_AUDIENCE: str | None = None
    # Tolerancia de reloj entre el gateway y este módulo, en segundos.
    JWT_LEEWAY_SECONDS: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
