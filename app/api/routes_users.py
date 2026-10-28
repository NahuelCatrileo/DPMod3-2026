"""Issue #11 · Registro, login y sesiones de usuarios (ADR-0009).

| Ruta                    | Acceso            | Qué hace                                       |
|-------------------------|-------------------|------------------------------------------------|
| POST /api/auth/register | público           | Crea la cuenta (contraseña con Argon2id)       |
| POST /api/auth/login    | público           | Correo y contraseña → JWT + cookie de sesión   |
| GET  /api/auth/me       | JWT de usuario    | Datos de la cuenta                             |
| POST /api/auth/refresh  | cookie de sesión  | Rota la cookie y entrega un JWT nuevo          |
| POST /api/auth/logout   | cookie de sesión  | Revoca la sesión y borra la cookie             |

El JWT va en el cuerpo, con la misma forma que POST /api/auth/token, y el
frontend lo guarda en memoria. El refresh token va solo en una cookie
HttpOnly: el JavaScript de la página no lo puede leer, así que un XSS no se
lo lleva. La cookie solo viaja a /api/auth, que es el mismo camino directo al
módulo y a través del gateway.

CSRF: refresh y logout se autentican con la cookie, que el navegador manda
solo. Por eso exigen la cabecera X-CSRF-Protection: un formulario de otro
sitio no puede ponerla, y un fetch desde otro origen la anuncia en un
preflight que el gateway solo acepta para los orígenes de la UI. Si viene
Origin, además tiene que estar en AUTH_ALLOWED_ORIGINS. Register y login no
la necesitan: solo aceptan JSON, que un formulario de otro sitio no puede
mandar.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from pydantic import AliasChoices, BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from app.api.auth import require_user
from app.api.routes_auth import TokenResponse
from app.api.schemas import ErrorResponse
from app.config import get_settings
from app.domain.errors import ForbiddenError, UnauthorizedError
from app.infra.db import get_session
from app.infra.models import User
from app.services import user_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "pubtube_refresh"
CSRF_HEADER = "X-CSRF-Protection"
_COOKIE_PATH = "/api/auth"

_401 = {401: {"model": ErrorResponse, "description": "UNAUTHORIZED"}}
_403_CSRF = {
    403: {
        "model": ErrorResponse,
        "description": f"UNAUTHORIZED: falta {CSRF_HEADER} o el origen no está permitido",
    }
}


class RegisterRequest(BaseModel):
    # Los campos de más (role, is_active...) se ignoran: el rol no se elige.
    # displayName es el nombre del contrato; display_name (la columna del
    # issue #11) y username (el campo del formulario de la UI) son alias.
    displayName: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = Field(
        ...,
        validation_alias=AliasChoices("displayName", "display_name", "username"),
        examples=["Ana Pérez"],
    )
    email: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        ),
    ] = Field(..., examples=["ana@example.com"])
    # El mínimo es el del formulario de registro de la UI. El máximo evita
    # que alguien mande megas para que Argon2id los procese.
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)
    # "Recordar sesión": la cookie sobrevive al cierre del navegador.
    remember: bool = False


class Account(BaseModel):
    id: str
    email: str
    displayName: str
    role: str
    createdAt: datetime


class AccountResponse(BaseModel):
    status: str = "ok"
    data: Account


def _account(user: User) -> AccountResponse:
    return AccountResponse(
        data=Account(
            id=str(user.id),
            email=user.email_normalized,
            displayName=user.display_name,
            role=user.role,
            createdAt=user.created_at,
        )
    )


def _require_csrf(request: Request) -> None:
    if not request.headers.get(CSRF_HEADER):
        raise ForbiddenError(f"Falta la cabecera {CSRF_HEADER}.")
    origin = request.headers.get("origin")
    permitidos = get_settings().AUTH_ALLOWED_ORIGINS
    if origin and permitidos and origin.rstrip("/") not in permitidos:
        raise ForbiddenError("El origen de la petición no está permitido.")


def _cookie_attrs() -> dict[str, Any]:
    settings = get_settings()
    return {
        "path": _COOKIE_PATH,
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
    }


def _borrar_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, **_cookie_attrs())


def _cookie_borrada() -> str:
    """Cabecera Set-Cookie que borra la sesión, para las respuestas de error."""
    response = Response()
    _borrar_cookie(response)
    return response.headers["set-cookie"]


def _tokens(response: Response, user: User, refresh: str, persistent: bool) -> TokenResponse:
    access_token, expires_in = user_auth.issue_user_access_token(user)
    # Sin max_age la cookie es de sesión: el navegador la borra al cerrarse.
    max_age = get_settings().AUTH_REFRESH_TOKEN_DAYS * 86400 if persistent else None
    response.set_cookie(REFRESH_COOKIE, refresh, max_age=max_age, **_cookie_attrs())
    # RFC 6749 §5.1: la respuesta con el token no se guarda en caché.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post(
    "/register",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ErrorResponse, "description": "DUPLICATE_CONTENT"},
        422: {"model": ErrorResponse, "description": "INVALID_METADATA"},
    },
    summary="Crear una cuenta de usuario (issue #11)",
)
def register(body: RegisterRequest, session: Session = Depends(get_session)) -> AccountResponse:
    user = user_auth.register_user(
        session, display_name=body.displayName, email=body.email, password=body.password
    )
    return _account(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={**_401, 422: {"model": ErrorResponse, "description": "INVALID_METADATA"}},
    summary="Iniciar sesión con correo y contraseña (issue #11)",
)
def login(
    body: LoginRequest, response: Response, session: Session = Depends(get_session)
) -> TokenResponse:
    user = user_auth.authenticate_user(session, body.email, body.password)
    if user is None:
        # Mismo mensaje para correo desconocido, contraseña incorrecta y
        # cuenta desactivada.
        raise UnauthorizedError("Correo o contraseña incorrectos.")
    refresh = user_auth.start_session(session, user, persistent=body.remember)
    return _tokens(response, user, refresh, body.remember)


@router.get(
    "/me",
    response_model=AccountResponse,
    responses={**_401, 403: {"model": ErrorResponse, "description": "UNAUTHORIZED: no es usuario"}},
    summary="Cuenta del usuario del token (issue #11)",
)
def me(
    claims: dict[str, Any] = Depends(require_user), session: Session = Depends(get_session)
) -> AccountResponse:
    try:
        user = session.get(User, uuid.UUID(claims["sub"]))
    except ValueError:
        # Un `sub` que no es UUID no puede ser de una cuenta.
        user = None
    if user is None or not user.is_active:
        raise UnauthorizedError("La cuenta del token no existe o está desactivada.")
    return _account(user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={**_401, **_403_CSRF},
    dependencies=[Depends(_require_csrf)],
    summary="Renovar el JWT con la cookie de sesión (issue #11)",
)
def refresh(
    response: Response,
    session: Session = Depends(get_session),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> TokenResponse:
    try:
        if not refresh_token:
            raise UnauthorizedError("Falta la cookie de sesión. Inicie sesión.")
        user, nuevo, persistent = user_auth.rotate_session(session, refresh_token)
    except UnauthorizedError as exc:
        # Una cookie que ya no sirve se borra, así el navegador deja de mandarla.
        exc.headers = {**(exc.headers or {}), "set-cookie": _cookie_borrada()}
        raise
    return _tokens(response, user, nuevo, persistent)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_403_CSRF,
    dependencies=[Depends(_require_csrf)],
    summary="Cerrar la sesión (issue #11)",
)
def logout(
    session: Session = Depends(get_session),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> Response:
    # Sin JWT: el de la sesión puede haber vencido y cerrar igual debe funcionar.
    user_auth.end_session(session, refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _borrar_cookie(response)
    return response
