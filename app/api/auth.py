"""Verificación de JWT en la API del módulo (ADR-0005).

El token lo emite el API Gateway (Equipo D, US-D1); aquí solo se verifica.
Llega en la cabecera `Authorization: Bearer <token>`.

Qué se exige:
- Firma válida con JWT_SECRET_KEY y el algoritmo configurado. Se pasa una
  lista cerrada de algoritmos a PyJWT, así un token con `alg: none` o con
  otro algoritmo se rechaza en vez de aceptarse sin firma.
- `exp` presente y no vencido, y `sub` presente (quién hace la petición).
- `iss` y `aud`, solo si están configurados.

Esto NO reemplaza el OAuth de Google: ese sigue siendo el que autoriza a
subir videos a YouTube (US-C3). JWT responde "¿quién llama a esta API?";
OAuth responde "¿con qué cuenta de YouTube publicamos?".
"""

from __future__ import annotations

import logging
from typing import Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.domain.errors import UnauthorizedError

logger = logging.getLogger(__name__)

# auto_error=False: si falta la cabecera respondemos con el sobre de error
# acordado (UNAUTHORIZED) en vez del {"detail": ...} de FastAPI.
_bearer = HTTPBearer(auto_error=False, description="JWT emitido por el API Gateway")


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    # Una variable vacía en .env cuenta como "no configurada".
    issuer = settings.JWT_ISSUER or None
    audience = settings.JWT_AUDIENCE or None
    required = ["exp", "sub"]
    if issuer:
        required.append("iss")
    if audience:
        required.append("aud")
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=issuer,
            audience=audience,
            leeway=settings.JWT_LEEWAY_SECONDS,
            options={"require": required},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("El token JWT está vencido.") from exc
    except jwt.InvalidTokenError as exc:
        # El detalle va al log, no a la respuesta: no le decimos al cliente
        # qué parte del token falló.
        logger.info("jwt_rechazado motivo=%s", type(exc).__name__)
        raise UnauthorizedError("El token JWT no es válido.") from exc


def require_jwt(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Dependencia de FastAPI. Devuelve los claims del token verificado."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Falta la cabecera Authorization: Bearer <token>.")
    return decode_token(credentials.credentials)
