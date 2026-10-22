"""US-C3.1 y US-C3.3 · Consentimiento de la cuenta de YouTube (ADR-0008).

Ningún test habla con Google. El canje del código se reemplaza en
`app.oauth.google.exchange_code` (pruebas del flujo) o en
`OAuth2Session.fetch_token` (pruebas de cómo se arma el canje).
"""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from oauthlib.oauth2.rfc6749.errors import InvalidClientError, InvalidGrantError
from requests_oauthlib import OAuth2Session

from app.config import get_settings
from app.domain.errors import (
    ErrorClass,
    ErrorCode,
    OAuthError,
    OAuthUnavailableError,
    classify,
)
from app.oauth import consent, google
from app.oauth.consent import PendingConsents
from app.oauth.token_store import GoogleTokens, load_tokens
from tests.conftest import make_token

AUTHORIZE = "/oauth2/authorize"
CALLBACK = "/oauth2/callback"
UPLOAD = google.SCOPE_YOUTUBE_UPLOAD


@pytest.fixture(autouse=True)
def _sin_consentimientos_pendientes():
    consent.pending_consents.clear()
    yield
    consent.pending_consents.clear()


@pytest.fixture
def canjes(monkeypatch):
    """Google acepta cualquier código y entrega tokens nuevos en cada canje."""
    pedidos = []

    def canjear(code, code_verifier):
        pedidos.append((code, code_verifier))
        return GoogleTokens(
            refresh_token=f"1//refresh-{len(pedidos)}",
            access_token=f"ya29.access-{len(pedidos)}",
            scopes=(UPLOAD,),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    monkeypatch.setattr(google, "exchange_code", canjear)
    return pedidos


def _pedir_url(admin_client) -> dict[str, str]:
    """Pide la URL de consentimiento y devuelve sus parámetros."""
    r = admin_client.post(AUTHORIZE)
    assert r.status_code == 200, r.text
    url = r.json()["data"]["authorizationUrl"]
    assert url.startswith(google.AUTH_URI + "?")
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _assert_oauth_error(r, status=400):
    assert r.status_code == status, r.text
    assert r.json()["status"] == "error"
    assert r.json()["code"] == "OAUTH_ERROR"


# --- Quién puede pedir el consentimiento ---------------------------------


def test_pedir_la_url_exige_jwt(anon_client):
    r = anon_client.post(AUTHORIZE)
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("role", [None, "service"])
def test_pedir_la_url_exige_role_admin(anon_client, role):
    """Un token de servicio no puede cambiar el canal donde se publica."""
    r = anon_client.post(
        AUTHORIZE, headers={"Authorization": f"Bearer {make_token(role=role)}"}
    )
    assert r.status_code == 403
    assert r.json()["code"] == "UNAUTHORIZED"
    assert len(consent.pending_consents) == 0


def test_sin_cliente_oauth_configurado_responde_503(admin_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "GOOGLE_CLIENT_ID", None)
    r = admin_client.post(AUTHORIZE)
    _assert_oauth_error(r, status=503)
    assert "GOOGLE_CLIENT_ID" in r.json()["message"]


# --- La URL de consentimiento (US-C3.1) -----------------------------------


def test_la_url_pide_acceso_offline_con_pkce_y_el_scope_minimo(admin_client):
    settings = get_settings()
    r = admin_client.post(AUTHORIZE)
    url = r.json()["data"]["authorizationUrl"]
    params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}

    assert params["response_type"] == "code"
    assert params["client_id"] == settings.GOOGLE_CLIENT_ID
    assert params["redirect_uri"] == settings.OAUTH_REDIRECT_URI
    assert params["scope"] == UPLOAD
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert "include_granted_scopes" not in params

    # PKCE S256: el challenge es el SHA-256 del verifier que quedó en el servidor.
    pendiente = consent.pending_consents.pop(params["state"])
    digest = hashlib.sha256(pendiente.code_verifier.encode()).digest()
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    # El verifier nunca sale del servidor.
    assert pendiente.code_verifier not in r.text


def test_cada_pedido_tiene_su_propio_state(admin_client):
    assert _pedir_url(admin_client)["state"] != _pedir_url(admin_client)["state"]


def test_la_respuesta_dice_cuanto_vale_la_url(admin_client):
    r = admin_client.post(AUTHORIZE)
    assert r.json()["data"]["expiresIn"] == consent.STATE_TTL_SECONDS


# --- La vuelta de Google (US-C3.3) ------------------------------------------


def test_el_callback_conecta_la_cuenta_y_no_exige_jwt(admin_client, canjes):
    params = _pedir_url(admin_client)

    # Quien vuelve es el navegador, sin JWT.
    del admin_client.headers["Authorization"]
    r = admin_client.get(CALLBACK, params={"state": params["state"], "code": "codigo-google"})

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["connected"] is True
    assert data["configured"] is True
    assert data["scopes"] == [UPLOAD]
    assert data["accessTokenExpiresAt"] and data["connectedAt"]
    # Ni el refresh ni el access token salen en la respuesta.
    assert "1//refresh" not in r.text and "ya29." not in r.text

    # Se canjeó el código con el verifier cuyo challenge iba en la URL.
    [(code, verifier)] = canjes
    assert code == "codigo-google"
    digest = hashlib.sha256(verifier.encode()).digest()
    assert params["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")

    assert load_tokens().refresh_token == "1//refresh-1"


def test_el_state_sirve_una_sola_vez(admin_client, canjes):
    params = _pedir_url(admin_client)
    pedido = {"state": params["state"], "code": "codigo-google"}

    assert admin_client.get(CALLBACK, params=pedido).status_code == 200
    _assert_oauth_error(admin_client.get(CALLBACK, params=pedido))
    assert len(canjes) == 1


@pytest.mark.parametrize("state", [None, "state-que-este-modulo-no-emitio"])
def test_un_state_desconocido_se_rechaza_sin_canjear(anon_client, canjes, state):
    """Protección CSRF: no se acepta una respuesta que ningún admin pidió."""
    params = {"code": "codigo-de-otra-cuenta"}
    if state:
        params["state"] = state

    _assert_oauth_error(anon_client.get(CALLBACK, params=params))
    assert canjes == []
    assert load_tokens() is None


def test_un_state_vencido_se_rechaza(admin_client, canjes, monkeypatch):
    ahora = [1000.0]
    monkeypatch.setattr(consent, "pending_consents", PendingConsents(clock=lambda: ahora[0]))
    params = _pedir_url(admin_client)

    ahora[0] += consent.STATE_TTL_SECONDS
    r = admin_client.get(CALLBACK, params={"state": params["state"], "code": "codigo"})

    _assert_oauth_error(r)
    assert canjes == []


def test_el_state_vale_hasta_su_vencimiento():
    ahora = [1000.0]
    pendientes = PendingConsents(ttl_seconds=600, clock=lambda: ahora[0])
    pendientes.add("vigente", "verifier", "admin")
    pendientes.add("vencido", "verifier", "admin")

    ahora[0] += 599
    assert pendientes.pop("vigente").code_verifier == "verifier"
    ahora[0] += 1
    assert pendientes.pop("vencido") is None


def test_si_se_rechaza_el_consentimiento_no_se_guarda_nada(admin_client, canjes):
    params = _pedir_url(admin_client)
    r = admin_client.get(CALLBACK, params={"state": params["state"], "error": "access_denied"})

    _assert_oauth_error(r)
    assert "access_denied" in r.json()["message"]
    assert canjes == []
    assert load_tokens() is None
    # El state ya se consumió: no se puede reusar para otro intento.
    assert consent.pending_consents.pop(params["state"]) is None


def test_un_callback_sin_codigo_se_rechaza(admin_client, canjes):
    params = _pedir_url(admin_client)
    _assert_oauth_error(admin_client.get(CALLBACK, params={"state": params["state"]}))
    assert canjes == []


def test_sin_permiso_para_subir_videos_no_se_guarda(admin_client, monkeypatch):
    monkeypatch.setattr(
        google,
        "exchange_code",
        lambda code, verifier: GoogleTokens(
            refresh_token="1//refresh",
            access_token="ya29.access",
            scopes=("https://www.googleapis.com/auth/youtube.readonly",),
            expires_at=None,
        ),
    )
    params = _pedir_url(admin_client)
    r = admin_client.get(CALLBACK, params={"state": params["state"], "code": "codigo"})

    _assert_oauth_error(r)
    assert "youtube.upload" in r.json()["message"]
    assert load_tokens() is None


def test_reconectar_reemplaza_la_cuenta_anterior(admin_client, canjes):
    for _ in range(2):
        params = _pedir_url(admin_client)
        r = admin_client.get(CALLBACK, params={"state": params["state"], "code": "codigo"})
        assert r.status_code == 200

    assert load_tokens().refresh_token == "1//refresh-2"


def test_si_google_no_responde_el_callback_responde_502(admin_client, monkeypatch):
    def sin_respuesta(code, verifier):
        raise OAuthUnavailableError("Google no respondió")

    monkeypatch.setattr(google, "exchange_code", sin_respuesta)
    params = _pedir_url(admin_client)
    r = admin_client.get(CALLBACK, params={"state": params["state"], "code": "codigo"})

    assert r.status_code == 502
    assert r.json()["code"] == "PUBLISH_FAILED"


# --- Cómo se canjea el código (google.exchange_code) ---------------------


def _google_responde(monkeypatch, respuesta=None, error=None):
    pedido = {}

    def fetch_token(self, token_url, **kwargs):
        pedido.update(kwargs, token_url=token_url, redirect_uri=self.redirect_uri)
        if error is not None:
            raise error
        return respuesta

    monkeypatch.setattr(OAuth2Session, "fetch_token", fetch_token)
    return pedido


def test_el_canje_envia_el_code_verifier_y_el_cliente_configurado(monkeypatch):
    settings = get_settings()
    pedido = _google_responde(
        monkeypatch,
        {
            "access_token": "ya29.a",
            "refresh_token": "1//r",
            "expires_in": 3599,
            "scope": [UPLOAD],
            "token_type": "Bearer",
        },
    )
    antes = datetime.now(timezone.utc)

    tokens = google.exchange_code("codigo", "verifier-del-servidor")

    assert pedido["token_url"] == google.TOKEN_URI
    assert pedido["code"] == "codigo"
    assert pedido["code_verifier"] == "verifier-del-servidor"
    assert pedido["client_secret"] == settings.GOOGLE_CLIENT_SECRET
    assert pedido["redirect_uri"] == settings.OAUTH_REDIRECT_URI
    # Se canjea con el código, no con la URL del callback (que es http).
    assert "authorization_response" not in pedido
    assert pedido["timeout"] == google.HTTP_TIMEOUT_SECONDS

    assert tokens.refresh_token == "1//r"
    assert tokens.access_token == "ya29.a"
    assert tokens.scopes == (UPLOAD,)
    assert timedelta(seconds=3598) <= tokens.expires_at - antes <= timedelta(seconds=3600)


def test_un_codigo_rechazado_es_oauth_error(monkeypatch):
    _google_responde(monkeypatch, error=InvalidGrantError())

    with pytest.raises(OAuthError) as exc:
        google.exchange_code("codigo-vencido", "verifier")
    assert "invalid_grant" in exc.value.message


def test_un_cliente_mal_configurado_apunta_a_la_configuracion(monkeypatch):
    """Es lo que responde Google si GOOGLE_CLIENT_SECRET está mal copiado."""
    _google_responde(monkeypatch, error=InvalidClientError())

    with pytest.raises(OAuthError) as exc:
        google.exchange_code("codigo", "verifier")
    assert exc.value.http_status == 503
    assert "GOOGLE_CLIENT_SECRET" in exc.value.message


def test_si_google_no_responde_al_canjear_es_transitorio(monkeypatch):
    _google_responde(monkeypatch, error=requests.ConnectionError("sin red"))

    with pytest.raises(OAuthUnavailableError) as exc:
        google.exchange_code("codigo", "verifier")
    assert exc.value.code is ErrorCode.PUBLISH_FAILED
    assert classify(exc.value.code) is ErrorClass.TRANSITORIO


def test_scopes_distintos_de_los_pedidos_no_rompen_el_canje(monkeypatch):
    """oauthlib lo avisa con una excepción Warning; el token llega igual."""
    readonly = "https://www.googleapis.com/auth/youtube.readonly"
    aviso = Warning("Scope has changed")
    aviso.token = {
        "access_token": "ya29.a",
        "refresh_token": "1//r",
        "expires_in": 3599,
        "scope": [UPLOAD, readonly],
    }
    _google_responde(monkeypatch, error=aviso)

    assert google.exchange_code("codigo", "verifier").scopes == (UPLOAD, readonly)


def test_sin_refresh_token_el_canje_se_rechaza(monkeypatch):
    _google_responde(
        monkeypatch, {"access_token": "ya29.a", "expires_in": 3599, "scope": [UPLOAD]}
    )

    with pytest.raises(OAuthError) as exc:
        google.exchange_code("codigo", "verifier")
    assert "refresh token" in exc.value.message
