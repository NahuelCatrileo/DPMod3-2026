"""US-C3.3 · Credenciales de Google vigentes para publicar (ADR-0008).

`get_credentials()` es lo que llama el publicador real antes de usar la
YouTube Data API:

- carga los tokens descifrados (token_store.py);
- si al access token le quedan menos de 3 min 45 s (el umbral de
  google-auth), pide uno nuevo con el refresh token y lo guarda;
- traduce los fallos de Google a la política de reintentos (ADR-0006), sin
  códigos nuevos:

  | Situación                                   | Código         | Clase       |
  |---------------------------------------------|----------------|-------------|
  | No hay cuenta conectada                     | OAUTH_ERROR    | definitivo  |
  | `invalid_grant`: acceso revocado o vencido  | OAUTH_ERROR    | definitivo  |
  | Otro rechazo de Google (`invalid_client`…)  | OAUTH_ERROR    | definitivo  |
  | Google no responde o devuelve 5xx           | PUBLISH_FAILED | transitorio |

  Con `invalid_grant` además se borra la credencial: ese refresh token ya no
  sirve y conservarlo haría que GET /oauth2/status diga que hay conexión.

Un lock serializa refresh, consentimiento y revocación. Sin él, dos jobs del
scheduler que publican a la vez refrescarían dos veces, y un refresh lento
podría escribir los tokens viejos encima de un consentimiento nuevo o
devolver a la base una cuenta recién revocada. Un lock del proceso alcanza
porque el módulo corre en una sola instancia (ADR-0003).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import timezone
from typing import NoReturn

from google.auth.credentials import TokenState
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials

from app.domain.errors import OAuthError, OAuthUnavailableError
from app.oauth import google
from app.oauth.token_store import GoogleTokens, delete_tokens, load_tokens, save_tokens

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def get_credentials(*, force_refresh: bool = False) -> Credentials:
    """Credenciales listas para la YouTube Data API.

    Lanza OAuthError (OAUTH_ERROR) u OAuthUnavailableError (PUBLISH_FAILED).
    El publicador las traduce con `PublishResult.failure(exc.code, exc.message)`.
    """
    with _lock:
        # Se lee dentro del lock: si otro hilo refrescó mientras este
        # esperaba, encuentra el token nuevo y no vuelve a pedirlo.
        tokens = load_tokens()
        if tokens is None:
            raise OAuthError(
                "La cuenta de YouTube no está conectada: un administrador debe "
                "autorizarla con POST /oauth2/authorize.",
                http_status=409,
            )
        credentials = google.build_credentials(tokens)
        # STALE (vence en menos de 3 min 45 s) también se refresca: una subida
        # larga no debería empezar con un token a punto de vencer.
        if force_refresh or credentials.token_state is not TokenState.FRESH:
            _refresh(credentials, tokens)
        return credentials


def store_consent(tokens: GoogleTokens) -> GoogleTokens:
    """Guarda los tokens de un consentimiento nuevo, en reemplazo de los
    anteriores."""
    with _lock:
        return save_tokens(tokens)


def revoke() -> bool:
    """Revoca el acceso en Google y borra los tokens. False si no había cuenta.

    Si Google no confirma, los tokens se conservan: borrarlos dejaría un
    acceso vivo en Google que ya nadie podría revocar desde el módulo.
    """
    with _lock:
        tokens = load_tokens()
        if tokens is None:
            return False
        google.revoke(tokens.refresh_token)
        delete_tokens()
        logger.warning("oauth_cuenta_desconectada")
        return True


def _refresh(credentials: Credentials, tokens: GoogleTokens) -> None:
    try:
        google.refresh(credentials)
    except RefreshError as exc:
        _raise_refresh_error(exc)
    except TransportError as exc:
        logger.warning("oauth_refresh_sin_respuesta motivo=%s", type(exc).__name__)
        raise OAuthUnavailableError(
            "No se pudo contactar a Google para refrescar el token."
        ) from exc

    nuevo = save_tokens(
        replace(
            tokens,
            access_token=credentials.token,
            # Google no suele rotar el refresh token; si lo hace, se guarda.
            refresh_token=credentials.refresh_token or tokens.refresh_token,
            expires_at=(
                credentials.expiry.replace(tzinfo=timezone.utc) if credentials.expiry else None
            ),
        )
    )
    logger.info(
        "oauth_token_refrescado vence=%s",
        nuevo.expires_at.isoformat() if nuevo.expires_at else None,
    )


def _raise_refresh_error(exc: RefreshError) -> NoReturn:
    error = _google_error(exc)
    if exc.retryable:
        logger.warning("oauth_refresh_fallo_transitorio error=%s", error)
        raise OAuthUnavailableError(
            f"Google no pudo refrescar el token por ahora ({error})."
        ) from exc
    if error == "invalid_grant":
        delete_tokens()
        logger.warning(
            "oauth_refresh_token_invalido: se borra la credencial, hay que volver a autorizar"
        )
        raise OAuthError(
            "Google rechazó el refresh token (invalid_grant): el acceso se revocó o "
            "venció. Un administrador debe volver a autorizar la cuenta con "
            "POST /oauth2/authorize.",
            http_status=409,
        ) from exc
    logger.warning("oauth_refresh_rechazado error=%s", error)
    pista = f" {google.CLIENT_ERROR_HINT}" if error in google.CLIENT_ERRORS else ""
    raise OAuthError(
        f"Google rechazó el refresh del token ({error}).{pista}", http_status=503
    ) from exc


def _google_error(exc: RefreshError) -> str:
    """Código de error OAuth de la respuesta de Google ('invalid_grant', ...)."""
    # google-auth pasa el cuerpo de la respuesta como segundo argumento.
    datos = exc.args[1] if len(exc.args) > 1 else None
    if isinstance(datos, dict) and datos.get("error"):
        return str(datos["error"])
    return "sin detalle"
