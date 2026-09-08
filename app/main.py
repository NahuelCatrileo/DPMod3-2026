from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from app.auth import create_authorization_url, exchange_code_for_token
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI(
    title="Módulo 3 - Publicación",
    description="Scheduler e integración con YouTube",
    version="1.0.0"
)

app.add_middleware(
    SessionMiddleware,
    secret_key="clave-secreta-desarrollo"
)

@app.get("/")
def root():
    return {
        "status": "ok",
        "module": "module-3"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.get("/oauth2/authorize")
def authorize(request: Request):
    authorization_url, state, code_verifier = create_authorization_url()

    request.session["state"] = state
    request.session["code_verifier"] = code_verifier

    return RedirectResponse(authorization_url)

@app.get("/oauth2/callback")
async def oauth_callback(request: Request):
    code_verifier = request.session.get("code_verifier")

    if not code_verifier:
        return {
            "status": "error",
            "message": "No se encontró el code_verifier de OAuth"
        }

    credentials = exchange_code_for_token(
        str(request.url),
        code_verifier
    )

    return {
        "status": "ok",
        "message": "Autenticación con Google completada"
    }