"""US-C3.1 · Cliente OAuth 2.0 de Google (ADR-0008).

Único módulo que habla con los endpoints OAuth de Google: arma la URL de
consentimiento, canjea el código, refresca y revoca. El resto de app/oauth/
usa estas funciones, así los tests reemplazan un solo punto y nada más toca
la red.

Las credenciales del cliente llegan por variables de entorno (12-factor), no
con `client_secret.json`: el archivo había que montarlo en el contenedor y
`.dockerignore` lo deja fuera de la imagen, con razón.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error

from app.config import get_settings
from app.domain.errors import OAuthError, OAuthUnavailableError
from app.oauth.token_store import GoogleTokens

logger = logging.getLogger(__name__)

# Mínimo privilegio: es el único scope que necesita videos.insert. Leer el
# canal (youtube.readonly) sería un permiso más que no se usa (ADR-0008).
SCOPE_YOUTUBE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPES = [SCOPE_YOUTUBE_UPLOAD]

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
# Para canjear y revocar. El refresh usa el timeout propio de google-auth.
HTTP_TIMEOUT_SECONDS = 15
# Errores con los que Google rechaza al cliente OAuth configurado, no al
# código ni al refresh token: el arreglo está en .env, no en reconectar.
CLIENT_ERRORS = frozenset({"invalid_client", "unauthorized_client"})
CLIENT_ERROR_HINT = "Revise GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en .env."


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _client() -> tuple[str, str]:
    """(client_id, client_secret), o OAuthError si falta alguno."""
    if not is_configured():
        raise OAuthError(
            "OAuth sin configurar: faltan GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET "
            "en .env (ver docs/oauth-youtube.md).",
            http_status=503,
        )
    settings = get_settings()
    return settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET


def _flow(code_verifier: str | None = None) -> Flow:
    client_id, client_secret = _client()
    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
            }
        },
        scopes=SCOPES,
        redirect_uri=get_settings().OAUTH_REDIRECT_URI,
        code_verifier=code_verifier,
        # from_client_config NO genera PKCE si no se le pide. Por eso el
        # callback del Sprint 0 nunca encontraba el code_verifier.
        autogenerate_code_verifier=code_verifier is None,
    )


def authorization_request() -> tuple[str, str, str]:
    """URL de consentimiento con PKCE S256. Devuelve (url, state, code_verifier)."""
    flow = _flow()
    url, state = flow.authorization_url(
        # Offline: Google entrega un refresh token, que permite publicar sin
        # nadie frente a la pantalla. Es el default de Flow; queda explícito.
        access_type="offline",
        # Google entrega el refresh token solo en el primer consentimiento.
        # Con prompt=consent lo entrega siempre, así reconectar funciona.
        prompt="consent",
    )
    return url, state, flow.code_verifier


def exchange_code(code: str, code_verifier: str) -> GoogleTokens:
    """Canjea el código de autorización por tokens, con el code_verifier."""
    flow = _flow(code_verifier)
    try:
        # Se pasa `code` y no la URL del callback: así oauthlib no exige HTTPS
        # en http://localhost, y el `state` ya lo validó consent.py.
        token = flow.fetch_token(code=code, timeout=HTTP_TIMEOUT_SECONDS)
    except Warning as cambio:
        # oauthlib avisa con una excepción Warning cuando Google concede
        # scopes distintos de los pedidos. El token llega igual; si falta
        # youtube.upload lo rechaza consent.py.
        token = cambio.token
    except OAuth2Error as exc:
        if exc.error in CLIENT_ERRORS:
            raise OAuthError(
                f"Google rechazó el cliente OAuth ({exc.error}). {CLIENT_ERROR_HINT}",
                http_status=503,
            ) from exc
        raise OAuthError(
            f"Google rechazó el código de autorización ({exc.error}). "
            "Pida una URL nueva con POST /oauth2/authorize."
        ) from exc
    except requests.RequestException as exc:
        raise OAuthUnavailableError(
            "Google no respondió al canjear el código de autorización."
        ) from exc

    if not token.get("refresh_token"):
        # No debería pasar con access_type=offline y prompt=consent, pero sin
        # refresh token el scheduler no podría publicar pasada una hora.
        raise OAuthError(
            "Google no entregó un refresh token. Revoque el acceso de la app en "
            "https://myaccount.google.com/permissions y vuelva a autorizar."
        )
    scopes = token.get("scope") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    expires_in = token.get("expires_in")
    return GoogleTokens(
        refresh_token=token["refresh_token"],
        access_token=token["access_token"],
        scopes=tuple(scopes),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
            if expires_in
            else None
        ),
    )


def build_credentials(tokens: GoogleTokens) -> Credentials:
    """Credentials de google-auth, las que espera la YouTube Data API."""
    client_id, client_secret = _client()
    return Credentials(
        token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(tokens.scopes),
        # google-auth compara el vencimiento contra la hora UTC sin zona.
        expiry=(
            tokens.expires_at.astimezone(timezone.utc).replace(tzinfo=None)
            if tokens.expires_at
            else None
        ),
    )


def refresh(credentials: Credentials) -> None:
    """Pide un access token nuevo. Deja pasar RefreshError y TransportError:
    cómo se traducen es decisión de credentials.py."""
    credentials.refresh(_http())


def revoke(token: str) -> None:
    """Revoca el token en Google. Revocar el refresh token invalida también
    los access tokens que se obtuvieron con él."""
    try:
        response = _http()(
            url=REVOKE_URI,
            method="POST",
            body=urlencode({"token": token}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except TransportError as exc:
        raise OAuthUnavailableError("Google no respondió al revocar el token.") from exc
    # 400 (invalid_token): ya estaba revocado o vencido, que es el resultado
    # buscado. Cualquier otra respuesta deja la revocación sin confirmar.
    if response.status == 400:
        logger.info("oauth_revocacion_token_ya_invalido")
    elif response.status != 200:
        raise OAuthUnavailableError(
            f"Google respondió {response.status} al revocar el token."
        )


def _http() -> Request:
    return Request()
