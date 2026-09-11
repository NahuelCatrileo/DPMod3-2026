"""Modelo de datos del Módulo 3.

Esquema acordado en la Guía Técnica §6:
    publication(id, content_id, state, schedule_at, timezone,
                youtube_video_id NULL, attempts, last_error NULL)

Añadimos created_at/updated_at y correlation_id, que no rompen nada
(son internos, no viajan en el contrato de eventos) pero son necesarios
para la trazabilidad exigida en Doc 3 §7.
"""

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.states import PublishState
from app.infra.db import Base, UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(dt_timezone.utc)


class Publication(Base):
    __tablename__ = "publication"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    content_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PublishState.PENDING.value, index=True
    )

    # Siempre en UTC. La zona horaria original se guarda aparte para poder
    # mostrarla y para auditar lo que pidió el creador.
    schedule_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, index=True
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    correlation_id: Mapped[str] = mapped_column(
        String(36), nullable=False, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Publication {self.id} content={self.content_id} state={self.state}>"
