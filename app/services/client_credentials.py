"""Emisión de JWT a clientes de servicio (ADR-0007).

Flujo *client credentials* de OAuth 2.0 (RFC 6749 §4.4): un sistema que
llama a la API se identifica con `client_id` y `client_secret` y recibe un
JWT de vida corta. Ese JWT lo validan el API Gateway (Equipo D, con la misma
clave en `JWT_SECRET`) y `app/api/auth.py`, así que sirve para llamar a
`/api/publish/*` a través del gateway o directo al módulo.

Los secretos se guardan como hash PBKDF2-SHA256 con sal (solo librería
estándar). Formato: `pbkdf2_sha256$<iteraciones>$<sal b64>$<hash b64>`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import AuthClient, get_settings

logger = logging.getLogger(__name__)

_ALGORITMO_HASH = "pbkdf2_sha256"
# Recomendación OWASP (2023) para PBKDF2-HMAC-SHA256.
ITERACIONES_POR_DEFECTO = 600_000
# Valores de ejemplo de config.py y .env.example: son públicos, así que
# firmar con ellos equivale a publicar la clave.
_CLAVES_DE_EJEMPLO = frozenset({"cambiame-en-.env", "genere-uno-con-openssl-rand-hex-32"})
# RFC 7518 §3.2: la clave de HS256 debe tener al menos 256 bits.
_LARGO_MINIMO_CLAVE = 32


def hash_secret(secret: str, iterations: int = ITERACIONES_POR_DEFECTO) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, iterations)
    return "$".join(
        [
            _ALGORITMO_HASH,
            str(iterations),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_secret(secret: str, stored_hash: str) -> bool:
    """Compara en tiempo constante. Un hash mal formado nunca valida."""
    try:
        algoritmo, iteraciones, salt_b64, digest_b64 = stored_hash.split("$")
        if algoritmo != _ALGORITMO_HASH:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        esperado = base64.b64decode(digest_b64, validate=True)
        calculado = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, int(iteraciones))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculado, esperado)


# Hash de relleno para clientes inexistentes: así un client_id desconocido
# tarda lo mismo que uno real con secreto incorrecto, y el tiempo de
# respuesta no revela qué client_id existen.
_HASH_RELLENO = hash_secret(secrets.token_urlsafe(16))


def authenticate_client(client_id: str, client_secret: str) -> AuthClient | None:
    cliente = get_settings().AUTH_CLIENTS.get(client_id)
    if cliente is None:
        verify_secret(client_secret, _HASH_RELLENO)
        return None
    if not verify_secret(client_secret, cliente.secret_hash):
        return None
    return cliente


def issue_access_token(client_id: str, cliente: AuthClient) -> tuple[str, int]:
    """Devuelve (token, segundos de vigencia)."""
    settings = get_settings()
    clave = settings.JWT_SECRET_KEY
    if settings.APP_ENV == "prod" and (
        clave in _CLAVES_DE_EJEMPLO or len(clave.encode()) < _LARGO_MINIMO_CLAVE
    ):
        raise RuntimeError(
            "JWT_SECRET_KEY no sirve para producción: genere una con openssl rand -hex 32"
        )

    vigencia = settings.JWT_ACCESS_TOKEN_MINUTES * 60
    ahora = datetime.now(timezone.utc)
    claims = {
        "sub": client_id,
        "role": cliente.role,
        "iat": ahora,
        "exp": ahora + timedelta(seconds=vigencia),
        # Identificador único: permite revocar o auditar un token concreto.
        "jti": str(uuid.uuid4()),
    }
    if settings.JWT_ISSUER:
        claims["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        claims["aud"] = settings.JWT_AUDIENCE

    token = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    logger.info("jwt_emitido client_id=%s jti=%s", client_id, claims["jti"])
    return token, vigencia
