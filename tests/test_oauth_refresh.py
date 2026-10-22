"""US-C3.3 · Refresh del access token y su traducción a la política de
reintentos de ADR-0006 (ADR-0008).

Ningún test habla con Google: se reemplaza `Credentials.refresh` de
google-auth, o `app.oauth.google._http` para la revocación.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials

from app.config import get_settings
from app.domain.errors import (
    ErrorClass,
    ErrorCode,
    OAuthError,
    OAuthUnavailableError,
    classify,
)
from app.oauth import google
from app.oauth.credentials import get_credentials
from app.oauth.token_store import GoogleTokens, load_tokens, save_tokens

REFRESH = "1//refresh-token-secreto"
UPLOAD = google.SCOPE_YOUTUBE_UPLOAD


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _conectar(access_token: str, vence_en: timedelta) -> GoogleTokens:
    return save_tokens(
        GoogleTokens(
            refresh_token=REFRESH,
            access_token=access_token,
            scopes=(UPLOAD,),
            expires_at=_ahora() + vence_en,
        )
    )


@pytest.fixture
def conectada() -> GoogleTokens:
    """Cuenta conectada con un access token que vale una hora más."""
    return _conectar("ya29.vigente", timedelta(hours=1))


@pytest.fixture
def vencida() -> GoogleTokens:
    """Cuenta conectada con el access token ya vencido."""
    return _conectar("ya29.vencido", timedelta(minutes=-1))


@pytest.fixture
def google_refresca(monkeypatch):
    """Google entrega un access token nuevo, válido por una hora."""
    llamadas = []

    def refresh(self, request):
        llamadas.append(self.refresh_token)
        self.token = f"ya29.nuevo-{len(llamadas)}"
        # google-auth trabaja con la hora UTC sin zona.
        self.expiry = _ahora().replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    return llamadas


def _google_falla(monkeypatch, error: Exception) -> None:
    def refresh(self, request):
        raise error

    monkeypatch.setattr(Credentials, "refresh", refresh)


def _invalid_grant() -> RefreshError:
    # Así la arma google-auth: mensaje y cuerpo de la respuesta de Google.
    return RefreshError(
        "invalid_grant: Token has been expired or revoked.",
        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
    )


# --- get_credentials(): el camino del publicador ---------------------------


def test_sin_cuenta_conectada_es_un_error_definitivo():
    with pytest.raises(OAuthError) as exc:
        get_credentials()
    assert exc.value.code is ErrorCode.OAUTH_ERROR
    assert classify(exc.value.code) is ErrorClass.DEFINITIVO
    assert "/oauth2/authorize" in exc.value.message


def test_las_credenciales_usan_el_cliente_de_la_configuracion(conectada, google_refresca):
    settings = get_settings()
    credentials = get_credentials()

    assert credentials.token == "ya29.vigente"
    assert credentials.refresh_token == REFRESH
    assert credentials.client_id == settings.GOOGLE_CLIENT_ID
    assert credentials.client_secret == settings.GOOGLE_CLIENT_SECRET
    assert credentials.token_uri == google.TOKEN_URI
    assert credentials.scopes == [UPLOAD]
    # Vigente: no se le pide nada a Google.
    assert google_refresca == []


def test_un_token_vencido_se_refresca_y_se_guarda(vencida, google_refresca):
    credentials = get_credentials()

    assert google_refresca == [REFRESH]
    assert credentials.token == "ya29.nuevo-1"
    guardados = load_tokens()
    assert guardados.access_token == "ya29.nuevo-1"
    assert guardados.expires_at > _ahora() + timedelta(minutes=59)
    assert guardados.connected_at == vencida.connected_at
    # Y el siguiente pedido ya no vuelve a Google.
    get_credentials()
    assert len(google_refresca) == 1


def test_un_token_a_punto_de_vencer_tambien_se_refresca(google_refresca):
    """google-auth lo considera STALE si le quedan menos de 3 min 45 s."""
    _conectar("ya29.por-vencer", timedelta(minutes=2))

    assert get_credentials().token == "ya29.nuevo-1"


def test_force_refresh_pide_un_token_aunque_el_actual_sirva(conectada, google_refresca):
    assert get_credentials(force_refresh=True).token == "ya29.nuevo-1"
    assert google_refresca == [REFRESH]


def test_si_google_rota_el_refresh_token_se_guarda_el_nuevo(vencida, monkeypatch):
    def refresh(self, request):
        self.token = "ya29.nuevo"
        self._refresh_token = "1//refresh-rotado"
        self.expiry = _ahora().replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    get_credentials()

    assert load_tokens().refresh_token == "1//refresh-rotado"


def test_invalid_grant_es_definitivo_y_borra_la_credencial(vencida, monkeypatch):
    """Acceso revocado por el dueño, o vencido a los 7 días en modo Testing."""
    _google_falla(monkeypatch, _invalid_grant())

    with pytest.raises(OAuthError) as exc:
        get_credentials()
    assert exc.value.http_status == 409
    assert classify(exc.value.code) is ErrorClass.DEFINITIVO
    assert "invalid_grant" in exc.value.message
    assert load_tokens() is None


def test_un_error_temporal_de_google_es_transitorio_y_conserva_la_credencial(
    vencida, monkeypatch
):
    _google_falla(
        monkeypatch, RefreshError("server_error", {"error": "server_error"}, retryable=True)
    )

    with pytest.raises(OAuthUnavailableError) as exc:
        get_credentials()
    assert exc.value.code is ErrorCode.PUBLISH_FAILED
    assert classify(exc.value.code) is ErrorClass.TRANSITORIO
    assert load_tokens().refresh_token == REFRESH


def test_sin_red_es_transitorio_y_conserva_la_credencial(vencida, monkeypatch):
    _google_falla(monkeypatch, TransportError("Connection reset by peer"))

    with pytest.raises(OAuthUnavailableError):
        get_credentials()
    assert load_tokens().refresh_token == REFRESH


def test_otro_rechazo_de_google_es_definitivo_pero_no_borra(vencida, monkeypatch):
    """invalid_client: el problema es la configuración, no el refresh token."""
    _google_falla(
        monkeypatch,
        RefreshError("invalid_client: Unauthorized", {"error": "invalid_client"}),
    )

    with pytest.raises(OAuthError) as exc:
        get_credentials()
    assert "invalid_client" in exc.value.message
    assert "GOOGLE_CLIENT_SECRET" in exc.value.message
    assert load_tokens() is not None


def test_dos_publicaciones_a_la_vez_refrescan_una_sola_vez(vencida, monkeypatch):
    llamadas = []

    def refresh_lento(self, request):
        llamadas.append(self.refresh_token)
        time.sleep(0.2)
        self.token = "ya29.nuevo"
        self.expiry = _ahora().replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh_lento)
    tokens = []
    hilos = [
        threading.Thread(target=lambda: tokens.append(get_credentials().token))
        for _ in range(2)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert llamadas == [REFRESH]
    assert tokens == ["ya29.nuevo", "ya29.nuevo"]


# --- Rutas de operación ------------------------------------------------------


def test_las_rutas_de_operacion_exigen_role_admin(client):
    for metodo, ruta in [
        ("get", "/oauth2/status"),
        ("post", "/oauth2/refresh"),
        ("post", "/oauth2/revoke"),
    ]:
        r = getattr(client, metodo)(ruta)
        assert r.status_code == 403, ruta
        assert r.json()["code"] == "UNAUTHORIZED"


def test_status_sin_cuenta_conectada(admin_client):
    r = admin_client.get("/oauth2/status")

    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "data": {
            "configured": True,
            "connected": False,
            "scopes": [],
            "accessTokenExpiresAt": None,
            "connectedAt": None,
            "updatedAt": None,
        },
    }


def test_status_muestra_la_conexion_sin_exponer_tokens(admin_client, conectada):
    r = admin_client.get("/oauth2/status")

    data = r.json()["data"]
    assert data["connected"] is True
    assert data["scopes"] == [UPLOAD]
    assert data["accessTokenExpiresAt"].endswith("Z")
    assert REFRESH not in r.text and "ya29." not in r.text


@pytest.mark.parametrize(
    "variable", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OAUTH_TOKEN_ENCRYPTION_KEY"]
)
def test_status_informa_si_falta_configuracion(admin_client, monkeypatch, variable):
    monkeypatch.setattr(get_settings(), variable, None)
    assert admin_client.get("/oauth2/status").json()["data"]["configured"] is False


def test_refresh_desde_la_api(admin_client, conectada, google_refresca):
    r = admin_client.post("/oauth2/refresh")

    assert r.status_code == 200
    assert google_refresca == [REFRESH]
    assert r.json()["data"]["connected"] is True
    assert load_tokens().access_token == "ya29.nuevo-1"


def test_refresh_sin_cuenta_responde_409(admin_client):
    r = admin_client.post("/oauth2/refresh")
    assert r.status_code == 409
    assert r.json()["code"] == "OAUTH_ERROR"


def test_refresh_con_google_caido_responde_502(admin_client, conectada, monkeypatch):
    _google_falla(monkeypatch, TransportError("timeout"))

    r = admin_client.post("/oauth2/refresh")
    assert r.status_code == 502
    assert r.json()["code"] == "PUBLISH_FAILED"


def test_refresh_con_acceso_revocado_desconecta_la_cuenta(admin_client, conectada, monkeypatch):
    _google_falla(monkeypatch, _invalid_grant())

    assert admin_client.post("/oauth2/refresh").status_code == 409
    assert admin_client.get("/oauth2/status").json()["data"]["connected"] is False


class _RevocacionEnGoogle:
    """Reemplaza el transporte HTTP de google-auth en google.revoke()."""

    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.pedidos: list[dict] = []

    def __call__(self, **pedido):
        self.pedidos.append(pedido)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status=self.status, data=b"{}")


def _google_revoca(monkeypatch, **kwargs) -> _RevocacionEnGoogle:
    google_http = _RevocacionEnGoogle(**kwargs)
    monkeypatch.setattr(google, "_http", lambda: google_http)
    return google_http


def test_revocar_desconecta_en_google_y_borra_los_tokens(admin_client, conectada, monkeypatch):
    google_http = _google_revoca(monkeypatch)

    r = admin_client.post("/oauth2/revoke")

    assert r.status_code == 200
    assert r.json()["data"]["connected"] is False
    assert load_tokens() is None
    [pedido] = google_http.pedidos
    assert pedido["url"] == google.REVOKE_URI
    assert pedido["method"] == "POST"
    # Se revoca el refresh token: arrastra a los access tokens emitidos con él.
    assert pedido["body"] == f"token={REFRESH.replace('/', '%2F')}"


def test_revocar_sin_cuenta_no_llama_a_google(admin_client, monkeypatch):
    google_http = _google_revoca(monkeypatch)

    r = admin_client.post("/oauth2/revoke")

    assert r.status_code == 200
    assert r.json()["data"]["connected"] is False
    assert google_http.pedidos == []


def test_un_token_ya_invalido_en_google_igual_se_desconecta(admin_client, conectada, monkeypatch):
    _google_revoca(monkeypatch, status=400)

    assert admin_client.post("/oauth2/revoke").status_code == 200
    assert load_tokens() is None


@pytest.mark.parametrize(
    "falla", [{"error": TransportError("sin red")}, {"status": 503}]
)
def test_si_google_no_confirma_la_revocacion_se_conservan_los_tokens(
    admin_client, conectada, monkeypatch, falla
):
    """Borrarlos dejaría vivo en Google un acceso que ya nadie podría revocar."""
    _google_revoca(monkeypatch, **falla)

    r = admin_client.post("/oauth2/revoke")

    assert r.status_code == 502
    assert r.json()["code"] == "PUBLISH_FAILED"
    assert load_tokens().refresh_token == REFRESH
