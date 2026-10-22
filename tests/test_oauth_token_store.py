"""US-C3.2 · Los tokens de Google se guardan cifrados en la base (ADR-0008)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import get_settings
from app.domain.errors import ErrorClass, ErrorCode, OAuthError, classify
from app.infra.db import SessionLocal
from app.infra.models import OAuthCredential
from app.oauth.token_store import (
    CREDENTIAL_ID,
    GoogleTokens,
    delete_tokens,
    load_tokens,
    save_tokens,
)

REFRESH = "1//refresh-token-secreto"
ACCESS = "ya29.access-token-secreto"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def _tokens(**cambios) -> GoogleTokens:
    datos = {
        "refresh_token": REFRESH,
        "access_token": ACCESS,
        "scopes": (SCOPE,),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    datos.update(cambios)
    return GoogleTokens(**datos)


def _fila() -> OAuthCredential:
    with SessionLocal() as session:
        return session.get(OAuthCredential, CREDENTIAL_ID)


def _filas() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(OAuthCredential))


def test_sin_cuenta_conectada_no_hay_tokens():
    assert load_tokens() is None


def test_guarda_y_recupera_los_tokens():
    guardados = save_tokens(_tokens())

    leidos = load_tokens()
    assert leidos.refresh_token == REFRESH
    assert leidos.access_token == ACCESS
    assert leidos.scopes == (SCOPE,)
    assert leidos.expires_at == guardados.expires_at
    assert leidos.connected_at is not None
    assert leidos.updated_at is not None


def test_en_la_base_ningun_token_queda_en_claro():
    save_tokens(_tokens())
    fila = _fila()

    columnas = " ".join(
        str(getattr(fila, c.key)) for c in OAuthCredential.__table__.columns
    )
    assert REFRESH not in columnas
    assert ACCESS not in columnas
    # Lo único legible son los scopes y las fechas.
    assert fila.scopes == SCOPE
    # Y lo cifrado se recupera solo con la clave configurada.
    claro = Fernet(get_settings().OAUTH_TOKEN_ENCRYPTION_KEY).decrypt(
        fila.encrypted_tokens.encode()
    )
    assert REFRESH.encode() in claro


def test_cada_guardado_cifra_distinto():
    """Fernet usa un IV aleatorio: el mismo token no deja siempre el mismo texto."""
    save_tokens(_tokens())
    primero = _fila().encrypted_tokens
    save_tokens(_tokens())
    assert _fila().encrypted_tokens != primero


def test_una_sola_fila_aunque_se_guarde_varias_veces():
    save_tokens(_tokens())
    save_tokens(_tokens(access_token="ya29.otro"))

    assert _filas() == 1
    assert load_tokens().access_token == "ya29.otro"


def test_un_refresh_conserva_la_fecha_de_conexion_y_un_consentimiento_la_reinicia():
    save_tokens(_tokens())
    antigua = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as session:
        session.get(OAuthCredential, CREDENTIAL_ID).connected_at = antigua
        session.commit()

    # Refresh: se guardan tokens que vienen de la base (traen connected_at).
    refrescados = save_tokens(replace(load_tokens(), access_token="ya29.nuevo"))
    assert refrescados.connected_at == antigua

    # Consentimiento nuevo: tokens recién entregados por Google (sin fecha).
    reconectados = save_tokens(_tokens())
    assert reconectados.connected_at > antigua


def test_con_otra_clave_los_tokens_no_se_leen(monkeypatch):
    save_tokens(_tokens())
    monkeypatch.setattr(
        get_settings(), "OAUTH_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )

    with pytest.raises(OAuthError) as exc:
        load_tokens()
    assert exc.value.http_status == 503
    assert REFRESH not in exc.value.message


def test_una_fila_alterada_se_rechaza():
    save_tokens(_tokens())
    with SessionLocal() as session:
        fila = session.get(OAuthCredential, CREDENTIAL_ID)
        cifrado = fila.encrypted_tokens
        medio = len(cifrado) // 2
        otro = "A" if cifrado[medio] != "A" else "B"
        fila.encrypted_tokens = cifrado[:medio] + otro + cifrado[medio + 1 :]
        session.commit()

    with pytest.raises(OAuthError):
        load_tokens()


@pytest.mark.parametrize("clave", [None, "", "no-es-una-clave-fernet"])
def test_sin_una_clave_valida_no_se_guarda_nada(monkeypatch, clave):
    monkeypatch.setattr(get_settings(), "OAUTH_TOKEN_ENCRYPTION_KEY", clave)

    with pytest.raises(OAuthError) as exc:
        save_tokens(_tokens())
    assert exc.value.http_status == 503
    assert "OAUTH_TOKEN_ENCRYPTION_KEY" in exc.value.message
    assert _filas() == 0


def test_un_problema_de_clave_no_se_reintenta():
    """OAUTH_ERROR es definitivo (ADR-0006): reintentar no arregla la clave."""
    assert classify(ErrorCode.OAUTH_ERROR) is ErrorClass.DEFINITIVO


def test_borrar_la_credencial():
    assert delete_tokens() is False
    save_tokens(_tokens())

    assert delete_tokens() is True
    assert load_tokens() is None
