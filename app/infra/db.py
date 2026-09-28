"""Acceso a la base de datos (SQLAlchemy 2.0)."""

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class UtcDateTime(TypeDecorator):
    """Datetime que SIEMPRE entra y sale en UTC y con tzinfo.

    Necesario porque SQLite (que usamos en los tests) descarta la zona horaria
    y devuelve datetimes naive. Sin esto, el mismo código se comporta distinto
    en test y en Postgres, que es la peor clase de bug: aparece recién en la
    demo.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Se intentó guardar un datetime sin zona horaria")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


_settings = get_settings()

# check_same_thread solo aplica a SQLite (lo usamos en los tests).
_connect_args = (
    {"check_same_thread": False} if _settings.DATABASE_URL.startswith("sqlite") else {}
)

engine = create_engine(
    _settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesión por request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
