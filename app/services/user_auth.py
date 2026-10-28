"""Issue #11 · Cuentas de usuario: registro, login y sesiones (ADR-0009).

Complementa a client_credentials.py: aquella entrega tokens a sistemas y
esta a personas. Las dos firman con `sign_access_token`, así que el gateway y
app/api/auth.py validan un token de usuario igual que uno de servicio. Lo que
cambia es `kind: user`, que limita al usuario a sus propias publicaciones, y
`role`, que sale de la cuenta.

- Contraseñas: Argon2id con el perfil de baja memoria de RFC 9106
  (64 MiB, 3 pasadas, 4 hilos), de argon2-cffi.
- Sesión: un refresh token opaco que viaja en una cookie HttpOnly. En la base
  solo queda su SHA-256; el token tiene 256 bits al azar, así que un hash
  lento no agrega nada. Cada refresh lo rota, y presentar uno ya rotado
  revoca toda la familia: alguien lo copió.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.errors import DuplicateUserError, UnauthorizedError
from app.infra.models import RefreshToken, User
from app.services.tokens import KIND_USER, sign_access_token

logger = logging.getLogger(__name__)

_hasher = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)

# Mismo mensaje para cualquier fallo de sesión: no se le dice al cliente si
# el token no existe, venció o fue revocado. El motivo va al log.
_SESION_INVALIDA = "La sesión no es válida. Inicie sesión de nuevo."


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Un hash mal formado nunca valida."""
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Hash de relleno para correos sin cuenta: así un correo desconocido tarda
# lo mismo que uno real con contraseña incorrecta, y el tiempo de respuesta
# no revela qué correos están registrados.
_HASH_RELLENO = hash_password(secrets.token_urlsafe(16))


_CORREO_DUPLICADO = "Ya existe una cuenta con ese correo."


def normalize_email(email: str) -> str:
    return email.strip().lower()


def register_user(
    session: Session, *, display_name: str, email: str, password: str
) -> User:
    email = normalize_email(email)
    existente = session.execute(
        select(User.id).where(User.email_normalized == email)
    ).first()
    if existente is not None:
        raise DuplicateUserError(_CORREO_DUPLICADO)

    user = User(
        email_normalized=email,
        display_name=display_name,
        password_hash=hash_password(password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        # Dos registros simultáneos con el mismo correo: la base decide.
        session.rollback()
        raise DuplicateUserError(_CORREO_DUPLICADO) from exc
    # El correo no va al log: es un dato personal.
    logger.info("usuario_registrado user_id=%s", user.id)
    return user


def authenticate_user(session: Session, email: str, password: str) -> User | None:
    user = session.execute(
        select(User).where(User.email_normalized == normalize_email(email))
    ).scalars().first()
    if user is None:
        verify_password(password, _HASH_RELLENO)
        return None
    if not verify_password(password, user.password_hash) or not user.is_active:
        return None
    # Si cambian los parámetros de Argon2id, el hash se actualiza en el
    # próximo login, que es el único momento en que se tiene la contraseña.
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.commit()
    return user


def issue_user_access_token(user: User) -> tuple[str, int]:
    """Devuelve (token, segundos de vigencia)."""
    token, vigencia, jti = sign_access_token(str(user.id), user.role, KIND_USER)
    logger.info("jwt_emitido user_id=%s jti=%s", user.id, jti)
    return token, vigencia


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _nuevo_refresh(
    session: Session, user_id: uuid.UUID, family_id: str, persistent: bool
) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=_hash_refresh(token),
            persistent=persistent,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=get_settings().AUTH_REFRESH_TOKEN_DAYS),
        )
    )
    return token


def _revocar_familia(session: Session, family_id: str) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


def start_session(session: Session, user: User, *, persistent: bool) -> str:
    """Abre una sesión nueva tras el login. Devuelve el refresh token en claro."""
    token = _nuevo_refresh(session, user.id, str(uuid.uuid4()), persistent)
    session.commit()
    return token


def rotate_session(session: Session, token: str) -> tuple[User, str, bool]:
    """Canjea un refresh token por otro. Devuelve (usuario, token nuevo, persistent)."""
    fila = session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(token))
    ).scalars().first()
    if fila is None:
        logger.info("refresh_rechazado motivo=desconocido")
        raise UnauthorizedError(_SESION_INVALIDA)

    # UPDATE condicional, como las transiciones de estado (US-C6): de dos
    # refresh simultáneos con el mismo token, solo uno revoca la fila.
    ahora = datetime.now(timezone.utc)
    revocada = session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == fila.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=ahora)
    )
    if revocada.rowcount != 1:
        _revocar_familia(session, fila.family_id)
        session.commit()
        logger.warning(
            "refresh_reutilizado user_id=%s family_id=%s", fila.user_id, fila.family_id
        )
        raise UnauthorizedError(_SESION_INVALIDA)

    user = session.get(User, fila.user_id)
    if fila.expires_at <= ahora or user is None or not user.is_active:
        session.commit()
        logger.info("refresh_rechazado motivo=vencido_o_cuenta_inactiva user_id=%s", fila.user_id)
        raise UnauthorizedError(_SESION_INVALIDA)

    nuevo = _nuevo_refresh(session, user.id, fila.family_id, fila.persistent)
    session.commit()
    return user, nuevo, fila.persistent


def end_session(session: Session, token: str | None) -> None:
    """Logout: revoca la sesión entera. Idempotente."""
    if not token:
        return
    fila = session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(token))
    ).scalars().first()
    if fila is None:
        return
    _revocar_familia(session, fila.family_id)
    session.commit()
    logger.info("sesion_cerrada user_id=%s family_id=%s", fila.user_id, fila.family_id)
