"""ADR-0007 · Emisión de JWT a clientes de servicio.

POST /api/auth/token sigue el flujo *client credentials* de OAuth 2.0
(RFC 6749 §4.4): el pedido va como formulario
(`application/x-www-form-urlencoded`) y la respuesta usa los nombres del
estándar (`access_token`, `token_type`, `expires_in`), así cualquier cliente
OAuth lo puede consumir. Los errores sí usan el sobre acordado del proyecto.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from app.api.schemas import ErrorResponse
from app.domain.errors import DomainError, ErrorCode, UnauthorizedError
from app.services.client_credentials import authenticate_client, issue_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

# auto_error=False: las credenciales también pueden venir en el formulario.
_basic = HTTPBasic(auto_error=False, description="client_id y client_secret")


class TokenResponse(BaseModel):
    """RFC 6749 §5.1"""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int


@router.post(
    "/token",
    response_model=TokenResponse,
    responses={
        401: {"model": ErrorResponse, "description": "UNAUTHORIZED"},
        422: {"model": ErrorResponse, "description": "INVALID_METADATA"},
    },
    summary="Emitir un JWT a un cliente de servicio (ADR-0007)",
)
def token(
    response: Response,
    grant_type: str = Form(..., examples=["client_credentials"]),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    basic: HTTPBasicCredentials | None = Depends(_basic),
) -> TokenResponse:
    if grant_type != "client_credentials":
        raise DomainError(
            ErrorCode.INVALID_METADATA,
            "grant_type no soportado. Use client_credentials.",
            http_status=422,
        )

    # RFC 6749 §2.3.1: las credenciales pueden venir por HTTP Basic o en el
    # cuerpo. Si llegan por ambos lados, manda la cabecera.
    if basic is not None:
        client_id, client_secret = basic.username, basic.password
    if not client_id or not client_secret:
        raise UnauthorizedError("Faltan client_id y client_secret.")

    cliente = authenticate_client(client_id, client_secret)
    if cliente is None:
        # Mismo mensaje para cliente inexistente y secreto incorrecto.
        raise UnauthorizedError("Credenciales de cliente inválidas.")

    access_token, expires_in = issue_access_token(client_id, cliente)
    # RFC 6749 §5.1: la respuesta con el token no se guarda en caché.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=access_token, expires_in=expires_in)
