"""US-C3.2 · Almacenamiento seguro de los tokens de Google (ADR-0008).

El refresh token permite subir videos al canal sin que nadie vuelva a dar
consentimiento: quien lo tenga publica en nombre del dueño del canal. Por eso:

- Va en la base (tabla `oauth_credential`) y no en un archivo. El contenedor
  se recrea en cada `docker compose up --build` y lo único que sobrevive es el
  volumen de PostgreSQL: con `credentials/token.json` había que volver a
  autorizar la cuenta después de cada rebuild.
- Va **cifrado** con Fernet (AES-128-CBC + HMAC-SHA256, librería
  `cryptography`) y la clave OAUTH_TOKEN_ENCRYPTION_KEY, que vive solo en
  .env. Un dump o un backup de la base no alcanzan para publicar en el canal,
  y por el HMAC una fila alterada se rechaza en vez de usarse.
- En claro quedan solo los scopes y las fechas, que no sirven para actuar en
  nombre de la cuenta y que GET /oauth2/status muestra.
- No se guarda el client_secret: al refrescar sale de la configuración, así
  rotarlo en Google Cloud no obliga a volver a autorizar.

Ningún token pasa por el log ni por una respuesta HTTP.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.domain.errors import OAuthError
from app.infra.db import SessionLocal
from app.infra.models import OAuthCredential

logger = logging.getLogger(__name__)

# Una sola cuenta conectada por instancia del módulo: la dueña del canal.
CREDENTIAL_ID = "youtube"


@dataclass(frozen=True)
class GoogleTokens:
    """Tokens de la cuenta de YouTube ya descifrados. Solo viven en memoria."""

    refresh_token: str
    access_token: str | None
    scopes: tuple[str, ...]
    # Vencimiento del access token, en UTC.
    expires_at: datetime | None
    # Fechas de la fila. None = tokens recién entregados por Google en un
    # consentimiento nuevo, que todavía no se guardaron.
    connected_at: datetime | None = None
    updated_at: datetime | None = None


def has_encryption_key() -> bool:
    return bool(get_settings().OAUTH_TOKEN_ENCRYPTION_KEY)


def _fernet() -> Fernet:
    key = get_settings().OAUTH_TOKEN_ENCRYPTION_KEY
    if not key:
        raise OAuthError(
            "Falta OAUTH_TOKEN_ENCRYPTION_KEY en .env: sin ella los tokens de Google "
            "no se pueden guardar cifrados (ver docs/oauth-youtube.md).",
            http_status=503,
        )
    try:
        return Fernet(key)
    except ValueError as exc:
        raise OAuthError(
            "OAUTH_TOKEN_ENCRYPTION_KEY no es una clave Fernet válida: deben ser "
            "32 bytes en base64 url-safe (ver docs/oauth-youtube.md).",
            http_status=503,
        ) from exc


def _encrypt(tokens: GoogleTokens) -> str:
    secretos = {"refresh_token": tokens.refresh_token, "access_token": tokens.access_token}
    return _fernet().encrypt(json.dumps(secretos).encode()).decode()


def _decrypt(row: OAuthCredential) -> GoogleTokens:
    try:
        secretos = json.loads(_fernet().decrypt(row.encrypted_tokens.encode()))
    except InvalidToken as exc:
        # La clave no es la que cifró, o la fila fue alterada (el HMAC no
        # coincide). No se distingue a propósito: en los dos casos no se usa.
        raise OAuthError(
            "Los tokens guardados no se pueden descifrar: OAUTH_TOKEN_ENCRYPTION_KEY "
            "cambió o la fila fue alterada. Restaure la clave anterior o vuelva a "
            "autorizar la cuenta con POST /oauth2/authorize.",
            http_status=503,
        ) from exc
    return GoogleTokens(
        refresh_token=secretos["refresh_token"],
        access_token=secretos["access_token"],
        scopes=tuple(row.scopes.split()),
        expires_at=row.expires_at,
        connected_at=row.connected_at,
        updated_at=row.updated_at,
    )


def load_tokens() -> GoogleTokens | None:
    """Tokens descifrados, o None si no hay ninguna cuenta conectada."""
    with SessionLocal() as session:
        row = session.get(OAuthCredential, CREDENTIAL_ID)
        return None if row is None else _decrypt(row)


def save_tokens(tokens: GoogleTokens) -> GoogleTokens:
    """Crea o reemplaza la credencial. Devuelve lo guardado, con sus fechas."""
    # Se cifra antes de abrir la sesión: sin clave válida no se toca la base.
    encrypted = _encrypt(tokens)
    with SessionLocal() as session:
        row = session.get(OAuthCredential, CREDENTIAL_ID)
        if row is None:
            row = OAuthCredential(id=CREDENTIAL_ID)
            session.add(row)
        if tokens.connected_at is None:
            # Consentimiento nuevo: la conexión cuenta desde ahora, aunque
            # reemplace a una cuenta conectada antes.
            row.connected_at = datetime.now(timezone.utc)
        row.encrypted_tokens = encrypted
        row.scopes = " ".join(tokens.scopes)
        row.expires_at = tokens.expires_at
        session.commit()
        session.refresh(row)
        return replace(tokens, connected_at=row.connected_at, updated_at=row.updated_at)


def delete_tokens() -> bool:
    """Borra la credencial. False si no había ninguna."""
    with SessionLocal() as session:
        row = session.get(OAuthCredential, CREDENTIAL_ID)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
