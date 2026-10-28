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

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
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
    # `sub` del JWT que la programó: el id del usuario o el client_id del
    # servicio (ADR-0009). Con un token de usuario solo se ven las propias.
    # NULL en las filas anteriores a la migración 0003.
    owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Publication {self.id} content={self.content_id} state={self.state}>"


class User(Base):
    """Issue #11 · Cuenta de una persona que usa PubTube (ADR-0009).

    Columnas de la tabla del issue #11, sección 2. El correo se guarda en
    minúsculas y sin espacios (`email_normalized`), así la unicidad no
    depende de cómo se escribió. La contraseña nunca se guarda: solo su hash
    Argon2id.
    """

    # "user" es palabra reservada en PostgreSQL.
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email_normalized: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Nombre que se muestra. No es único: dos personas pueden llamarse igual.
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Va en el claim `role` del JWT. El registro siempre crea "user"; otro
    # rol (por ejemplo "admin") se asigna en la base.
    role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="user", server_default="user"
    )
    # Desactivar una cuenta corta el login y el refresh sin borrar su historial.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )


class RefreshToken(Base):
    """Issue #11 · Una sesión de usuario (ADR-0009).

    El token es opaco y viaja en una cookie HttpOnly. Aquí solo queda su
    SHA-256: quien lea la tabla no puede usarlo. Cada refresh revoca la fila
    y crea otra con el mismo `family_id`; presentar una fila ya revocada
    revoca toda la familia, porque significa que alguien copió el token.
    """

    __tablename__ = "refresh_token"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # "Recordar sesión": la cookie sobrevive al cierre del navegador.
    persistent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )


class OAuthCredential(Base):
    """US-C3.2 · Tokens de Google de la cuenta de YouTube (ADR-0008).

    Tabla interna del módulo: no viaja en ningún contrato. Hoy hay una sola
    fila, la de la cuenta dueña del canal (id 'youtube').

    El refresh token y el access token van juntos y cifrados en
    `encrypted_tokens` (Fernet, ver app/oauth/token_store.py). En claro solo
    queda lo que no sirve para actuar en nombre de la cuenta.
    """

    __tablename__ = "oauth_credential"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    encrypted_tokens: Mapped[str] = mapped_column(Text, nullable=False)
    # Scopes otorgados, separados por espacio (formato de OAuth 2.0).
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    # Vencimiento del access token. El refresh token no tiene uno conocido.
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # Último consentimiento. Reconectar la cuenta lo reinicia; un refresh no.
    connected_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )
