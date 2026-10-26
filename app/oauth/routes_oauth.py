"""US-C3 · Rutas OAuth 2.0 con Google (ADR-0008).

| Ruta                   | Acceso             | Qué hace                                    |
|------------------------|--------------------|---------------------------------------------|
| POST /oauth2/authorize | JWT con role admin | Devuelve la URL de consentimiento de Google |
| GET  /oauth2/callback  | público            | Canjea el código y guarda tokens cifrados   |
| GET  /oauth2/status    | JWT con role admin | Si hay cuenta conectada; nunca los tokens   |
| POST /oauth2/refresh   | JWT con role admin | Fuerza un refresh: ¿el acceso sigue vivo?   |
| POST /oauth2/revoke    | JWT con role admin | Revoca en Google y borra los tokens         |

El callback es la única ruta pública porque quien llega es el navegador que
Google redirige, y ese navegador no lleva el JWT. Lo protegen el `state` de un
solo uso y PKCE (ver consent.py).

Antes (Sprint 0) todo /oauth2/* era público (ADR-0005) y cualquiera podía
conectar su propia cuenta en lugar de la del canal; ver ADR-0008.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.api.schemas import ErrorResponse
from app.oauth import consent, credentials, google
from app.oauth.token_store import GoogleTokens, has_encryption_key, load_tokens

router = APIRouter(prefix="/oauth2", tags=["oauth"])

_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "UNAUTHORIZED: falta el JWT o no es válido"},
    403: {"model": ErrorResponse, "description": "UNAUTHORIZED: el JWT no trae role admin"},
    503: {"model": ErrorResponse, "description": "OAUTH_ERROR: OAuth sin configurar"},
}


class AuthorizationUrl(BaseModel):
    authorizationUrl: str
    # Segundos que vale la URL: después el callback la rechaza.
    expiresIn: int


class AuthorizationUrlResponse(BaseModel):
    status: str = "ok"
    data: AuthorizationUrl


class Connection(BaseModel):
    """Estado de la conexión con YouTube. Nunca incluye tokens."""

    # GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y OAUTH_TOKEN_ENCRYPTION_KEY.
    configured: bool
    connected: bool
    scopes: list[str] = Field(default_factory=list)
    accessTokenExpiresAt: datetime | None = None
    connectedAt: datetime | None = None
    updatedAt: datetime | None = None


class ConnectionResponse(BaseModel):
    status: str = "ok"
    data: Connection


def _connection(tokens: GoogleTokens | None) -> ConnectionResponse:
    configured = google.is_configured() and has_encryption_key()
    if tokens is None:
        return ConnectionResponse(data=Connection(configured=configured, connected=False))
    return ConnectionResponse(
        data=Connection(
            configured=configured,
            connected=True,
            scopes=list(tokens.scopes),
            accessTokenExpiresAt=tokens.expires_at,
            connectedAt=tokens.connected_at,
            updatedAt=tokens.updated_at,
        )
    )


@router.post(
    "/authorize",
    response_model=AuthorizationUrlResponse,
    responses=_ADMIN_ERRORS,
    summary="URL de consentimiento de Google para conectar la cuenta de YouTube (US-C3.3)",
)
def authorize(claims: dict[str, Any] = Depends(require_admin)) -> AuthorizationUrlResponse:
    # Devuelve la URL en vez de redirigir: quien llama manda un JWT, cosa que
    # un navegador no hace al navegar, así que la URL se abre aparte.
    url = consent.start_consent(claims["sub"])
    return AuthorizationUrlResponse(
        data=AuthorizationUrl(authorizationUrl=url, expiresIn=consent.STATE_TTL_SECONDS)
    )


@router.get(
    "/callback",
    response_model=ConnectionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "OAUTH_ERROR"},
        502: {"model": ErrorResponse, "description": "PUBLISH_FAILED: Google no respondió"},
        503: {"model": ErrorResponse, "description": "OAUTH_ERROR: OAuth sin configurar"},
    },
    summary="Vuelta del consentimiento de Google (la llama el navegador)",
)
def callback(
    state: str | None = None, code: str | None = None, error: str | None = None
) -> ConnectionResponse:
    return _connection(consent.complete_consent(state=state, code=code, error=error))


@router.get(
    "/status",
    response_model=ConnectionResponse,
    responses=_ADMIN_ERRORS,
    dependencies=[Depends(require_admin)],
    summary="¿Hay una cuenta de YouTube conectada? (US-C3)",
)
def oauth_status() -> ConnectionResponse:
    return _connection(load_tokens())


@router.post(
    "/refresh",
    response_model=ConnectionResponse,
    responses={
        **_ADMIN_ERRORS,
        409: {"model": ErrorResponse, "description": "OAUTH_ERROR: no hay cuenta conectada"},
        502: {"model": ErrorResponse, "description": "PUBLISH_FAILED: Google no respondió"},
    },
    dependencies=[Depends(require_admin)],
    summary="Forzar un refresh del access token (US-C3.3)",
)
def refresh() -> ConnectionResponse:
    # El mismo camino que usa el publicador: si esto funciona, publicar
    # no va a fallar por la autorización.
    credentials.get_credentials(force_refresh=True)
    return _connection(load_tokens())


@router.post(
    "/revoke",
    response_model=ConnectionResponse,
    responses={
        **_ADMIN_ERRORS,
        502: {"model": ErrorResponse, "description": "PUBLISH_FAILED: Google no respondió"},
    },
    dependencies=[Depends(require_admin)],
    summary="Desconectar la cuenta de YouTube: revoca en Google y borra los tokens",
)
def revoke() -> ConnectionResponse:
    credentials.revoke()
    return _connection(None)
