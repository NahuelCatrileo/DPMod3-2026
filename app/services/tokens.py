"""Firma de los JWT que emite el módulo (ADR-0007 y ADR-0009).

Un solo lugar para los claims comunes y la guarda de producción. Lo usan los
clientes de servicio (client credentials) y los usuarios (login y refresh),
así que un token de usuario pasa exactamente la misma verificación que uno de
servicio, en el gateway y en app/api/auth.py. Lo único que los distingue es
el claim `role`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

# Claim `role` de los tokens que entrega el login (ADR-0009). Un usuario solo
# ve las publicaciones que programó él; los demás roles conservan el acceso
# entre servicios de ADR-0005.
USER_ROLE = "user"

# Valores de ejemplo de config.py y .env.example: son públicos, así que
# firmar con ellos equivale a publicar la clave.
_CLAVES_DE_EJEMPLO = frozenset({"cambiame-en-.env", "genere-uno-con-openssl-rand-hex-32"})
# RFC 7518 §3.2: la clave de HS256 debe tener al menos 256 bits.
_LARGO_MINIMO_CLAVE = 32


def sign_access_token(sub: str, role: str) -> tuple[str, int, str]:
    """Devuelve (token, segundos de vigencia, jti)."""
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
        "sub": sub,
        "role": role,
        "iat": ahora,
        "exp": ahora + timedelta(seconds=vigencia),
        # Identificador único: permite revocar o auditar un token concreto.
        "jti": str(uuid.uuid4()),
    }
    if settings.JWT_ISSUER:
        claims["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        claims["aud"] = settings.JWT_AUDIENCE

    token = jwt.encode(claims, clave, algorithm=settings.JWT_ALGORITHM)
    return token, vigencia, claims["jti"]
