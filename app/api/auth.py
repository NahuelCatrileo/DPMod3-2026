"""Verificación de JWT en la API del módulo (ADR-0005).

El token lo emite este módulo: a sistemas en POST /api/auth/token (ADR-0007)
y a personas en POST /api/auth/login (ADR-0009). El API Gateway (Equipo D,
US-D1) lo valida y lo reenvía, y aquí se verifica de nuevo. Llega en la
cabecera `Authorization: Bearer <token>`.

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
from app.domain.errors import ForbiddenError, UnauthorizedError
from app.services.tokens import KIND_SERVICE, KIND_USER, USER_ROLE

logger = logging.getLogger(__name__)

# auto_error=False: si falta la cabecera respondemos con el sobre de error
# acordado (UNAUTHORIZED) en vez del {"detail": ...} de FastAPI.
_bearer = HTTPBearer(
    auto_error=False, description="JWT emitido por POST /api/auth/token o /api/auth/login"
)

# Rol que permite conectar o desconectar la cuenta de YouTube (ADR-0008).
ADMIN_ROLE = "admin"


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


def require_admin(claims: dict[str, Any] = Depends(require_jwt)) -> dict[str, Any]:
    """Dependencia de FastAPI: JWT válido y con `role: admin` (ADR-0008).

    Conectar la cuenta de YouTube decide a qué canal se suben los videos de
    todos los creadores, así que un token de servicio común no alcanza.
    """
    if claims.get("role") != ADMIN_ROLE:
        raise ForbiddenError(f"Esta operación requiere un token con role '{ADMIN_ROLE}'.")
    return claims


def token_kind(claims: dict[str, Any]) -> str:
    """`user` o `service`, según el claim `kind` (ADR-0009).

    Los tokens sin `kind` son de antes de ese claim: los de usuario se
    reconocen por `role: user`, y el resto se trata como servicio, igual que
    hasta ahora.
    """
    kind = claims.get("kind")
    if kind in (KIND_USER, KIND_SERVICE):
        return kind
    return KIND_USER if claims.get("role") == USER_ROLE else KIND_SERVICE


def require_user(claims: dict[str, Any] = Depends(require_jwt)) -> dict[str, Any]:
    """Dependencia de FastAPI: JWT válido de un usuario (ADR-0009).

    Un token de servicio no representa a una persona, así que no tiene
    cuenta que mostrar en /api/auth/me.
    """
    if token_kind(claims) != KIND_USER:
        raise ForbiddenError(
            "Esta operación requiere el token de un usuario (POST /api/auth/login)."
        )
    return claims


def owner_scope(claims: dict[str, Any]) -> str | None:
    """Dueño al que se limita la consulta, o None si el token ve todo (ADR-0009).

    Con un token de usuario es su `sub`: solo ve lo que programó él, salvo que
    su cuenta tenga `role: admin`. Los servicios siguen viendo todas las
    publicaciones, como antes.
    """
    if token_kind(claims) == KIND_USER and claims.get("role") != ADMIN_ROLE:
        return claims["sub"]
    return None
