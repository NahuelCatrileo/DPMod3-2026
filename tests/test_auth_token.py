"""ADR-0007 · POST /api/auth/token emite JWT a clientes de servicio."""

import base64

import jwt
import pytest

from app.config import AuthClient, get_settings
from app.services.client_credentials import hash_secret, verify_secret

TOKEN_URL = "/api/auth/token"
SECRETO = "secreto-del-gateway"


@pytest.fixture
def clientes(monkeypatch):
    # Pocas iteraciones para que la suite sea rápida; el formato es el mismo.
    monkeypatch.setattr(
        get_settings(),
        "AUTH_CLIENTS",
        {"gateway": AuthClient(secret_hash=hash_secret(SECRETO, iterations=1000))},
    )


def _pedir(anon_client, **form):
    datos = {"grant_type": "client_credentials", **form}
    return anon_client.post(TOKEN_URL, data=datos)


def _assert_unauthorized(r):
    assert r.status_code == 401
    assert r.json() == {
        "status": "error",
        "code": "UNAUTHORIZED",
        "message": r.json()["message"],
    }
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_emite_token_con_credenciales_en_el_formulario(anon_client, clientes):
    r = _pedir(anon_client, client_id="gateway", client_secret=SECRETO)

    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == get_settings().JWT_ACCESS_TOKEN_MINUTES * 60
    assert r.headers["Cache-Control"] == "no-store"

    settings = get_settings()
    claims = jwt.decode(
        body["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert claims["sub"] == "gateway"
    assert claims["role"] == "service"
    assert claims["exp"] - claims["iat"] == body["expires_in"]
    assert claims["jti"]


def test_emite_token_con_http_basic(anon_client, clientes):
    basic = base64.b64encode(f"gateway:{SECRETO}".encode()).decode()
    r = anon_client.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    assert r.status_code == 200


def test_el_token_emitido_sirve_para_la_api(anon_client, clientes):
    token = _pedir(anon_client, client_id="gateway", client_secret=SECRETO).json()["access_token"]
    r = anon_client.get(
        "/api/publish/no-existe/status", headers={"Authorization": f"Bearer {token}"}
    )
    # Pasa la autenticación y llega a la ruta, que responde 404.
    assert r.status_code == 404


def test_cada_token_tiene_jti_distinto(anon_client, clientes):
    def jti():
        token = _pedir(anon_client, client_id="gateway", client_secret=SECRETO).json()[
            "access_token"
        ]
        return jwt.decode(token, options={"verify_signature": False})["jti"]

    assert jti() != jti()


def test_secreto_incorrecto(anon_client, clientes):
    _assert_unauthorized(_pedir(anon_client, client_id="gateway", client_secret="otro"))


def test_cliente_inexistente_responde_igual_que_secreto_incorrecto(anon_client, clientes):
    inexistente = _pedir(anon_client, client_id="nadie", client_secret=SECRETO)
    incorrecto = _pedir(anon_client, client_id="gateway", client_secret="otro")
    _assert_unauthorized(inexistente)
    assert inexistente.json() == incorrecto.json()


def test_sin_credenciales(anon_client, clientes):
    _assert_unauthorized(_pedir(anon_client))


def test_sin_clientes_configurados_nadie_obtiene_token(anon_client):
    _assert_unauthorized(_pedir(anon_client, client_id="gateway", client_secret=SECRETO))


def test_grant_type_no_soportado(anon_client, clientes):
    r = anon_client.post(
        TOKEN_URL,
        data={"grant_type": "password", "client_id": "gateway", "client_secret": SECRETO},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_METADATA"


def test_falta_grant_type(anon_client, clientes):
    r = anon_client.post(TOKEN_URL, data={"client_id": "gateway", "client_secret": SECRETO})
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_METADATA"


def test_rol_e_issuer_audience_configurados(anon_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "AUTH_CLIENTS",
        {"modulo-4": AuthClient(secret_hash=hash_secret(SECRETO, iterations=1000), role="admin")},
    )
    monkeypatch.setattr(settings, "JWT_ISSUER", "pubtube-m3")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "pubtube")

    token = _pedir(anon_client, client_id="modulo-4", client_secret=SECRETO).json()["access_token"]
    claims = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        issuer="pubtube-m3",
        audience="pubtube",
    )
    assert claims["role"] == "admin"
    # Y la propia API lo acepta con esa misma configuración.
    r = anon_client.get(
        "/api/publish/no-existe/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 404


def test_vigencia_configurable(anon_client, clientes, monkeypatch):
    monkeypatch.setattr(get_settings(), "JWT_ACCESS_TOKEN_MINUTES", 5)
    r = _pedir(anon_client, client_id="gateway", client_secret=SECRETO)
    assert r.json()["expires_in"] == 300


@pytest.mark.parametrize(
    "clave",
    [
        "cambiame-en-.env",  # default de config.py
        "genere-uno-con-openssl-rand-hex-32",  # el de .env.example, tal cual
        "corta",  # menos de 256 bits
    ],
)
def test_en_produccion_no_firma_con_una_clave_insegura(clientes, monkeypatch, clave):
    from app.services.client_credentials import issue_access_token

    settings = get_settings()
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", clave)
    with pytest.raises(RuntimeError):
        issue_access_token("gateway", settings.AUTH_CLIENTS["gateway"])


def test_en_produccion_firma_con_una_clave_generada(clientes, monkeypatch):
    from app.services.client_credentials import issue_access_token

    settings = get_settings()
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "a" * 64)  # largo de openssl rand -hex 32
    token, _ = issue_access_token("gateway", settings.AUTH_CLIENTS["gateway"])
    assert jwt.decode(token, "a" * 64, algorithms=[settings.JWT_ALGORITHM])["sub"] == "gateway"


def test_auth_clients_se_lee_como_json_desde_el_entorno(monkeypatch):
    from app.config import Settings

    h = hash_secret(SECRETO, iterations=1000)
    monkeypatch.setenv("AUTH_CLIENTS", f'{{"gateway": {{"secret_hash": "{h}"}}}}')
    settings = Settings()
    assert settings.AUTH_CLIENTS["gateway"].role == "service"
    assert verify_secret(SECRETO, settings.AUTH_CLIENTS["gateway"].secret_hash)


def test_auth_clients_vacio_significa_sin_clientes(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("AUTH_CLIENTS", "")
    assert Settings().AUTH_CLIENTS == {}


@pytest.mark.parametrize(
    "hash_guardado",
    [
        "",
        "texto-plano",
        "md5$1000$c2Fs$aGFzaA==",
        "pbkdf2_sha256$no-numero$c2Fs$aGFzaA==",
        "pbkdf2_sha256$1000$no-es-base64!$aGFzaA==",
    ],
)
def test_hash_mal_formado_nunca_valida(hash_guardado):
    assert verify_secret(SECRETO, hash_guardado) is False


def test_hash_tiene_sal_distinta_cada_vez():
    a = hash_secret(SECRETO, iterations=1000)
    b = hash_secret(SECRETO, iterations=1000)
    assert a != b
    assert verify_secret(SECRETO, a) and verify_secret(SECRETO, b)
    assert not verify_secret("otro", a)
