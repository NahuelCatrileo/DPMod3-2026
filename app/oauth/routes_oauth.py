"""OAuth 2.0 con Google (preparación de US-C3, Sprint 3).

Se conserva el trabajo ya hecho por el equipo, con tres correcciones:

1. Rutas y secretos vienen de la configuración, no hardcodeados.
2. El token se guarda como JSON, no con pickle. `pickle.load` sobre un archivo
   que un atacante pueda escribir ejecuta código arbitrario; además el pickle
   de un objeto Credentials se rompe al actualizar la librería.
3. El token va a `credentials/`, que ya está en .gitignore.

Este módulo NO participa del incremento del Sprint 1 (PUBLISHER_MODE=mock).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth2", tags=["oauth"])

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _flow():
    from google_auth_oauthlib.flow import Flow

    settings = get_settings()
    if settings.APP_ENV == "dev":
        # Permite redirect_uri http://localhost en desarrollo.
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    return Flow.from_client_secrets_file(
        settings.GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=settings.OAUTH_REDIRECT_URI,
    )


@router.get("/authorize")
def authorize(request: Request):
    flow = _flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    request.session["state"] = state
    request.session["code_verifier"] = flow.code_verifier
    return RedirectResponse(authorization_url)


@router.get("/callback")
def callback(request: Request):
    code_verifier = request.session.get("code_verifier")
    if not code_verifier:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "OAUTH_ERROR",
                "message": "No se encontró el code_verifier de la sesión.",
            },
        )

    flow = _flow()
    flow.fetch_token(
        authorization_response=str(request.url), code_verifier=code_verifier
    )

    settings = get_settings()
    token_path = Path(settings.GOOGLE_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(flow.credentials.to_json(), encoding="utf-8")
    os.chmod(token_path, 0o600)

    logger.info("oauth_token_guardado path=%s", token_path)
    return {"status": "ok", "data": {"message": "Autenticación con Google completada"}}


def load_credentials():
    """Usado por YouTubePublisher en Sprint 3."""
    from google.oauth2.credentials import Credentials

    settings = get_settings()
    path = Path(settings.GOOGLE_TOKEN_FILE)
    if not path.exists():
        return None
    return Credentials.from_authorized_user_info(
        json.loads(path.read_text(encoding="utf-8")), SCOPES
    )
